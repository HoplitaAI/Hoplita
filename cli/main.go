package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	neturl "net/url"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"gopkg.in/ini.v1"
)

const HOPLITAVersion = "1.0"

type PoolModel struct {
	Name    string
	BaseURL string
	APIKey  string
	Model   string
	Profile string
}

type Config struct {
	BaseURL       string
	APIKey        string
	Model         string
	Timeout       int
	MaxIter       int
	MCPURL        string
	Language      string
	MultiNumCtx   int
	Pool          map[string]PoolModel
	modelFixed    bool
	SkillCategory string
	CompactKeep   int
	QAReview      string
}

func loadConfig(path string) (Config, error) {
	f, err := ini.Load(path)
	if err != nil {
		return Config{}, fmt.Errorf("loading %s: %w", path, err)
	}
	api := f.Section("API")
	chat := f.Section("CHAT")
	mcp := f.Section("MCP")
	app := f.Section("APP")
	cfg := Config{
		BaseURL:  api.Key("base_url").String(),
		APIKey:   api.Key("api_key").String(),
		Model:    api.Key("model").String(),
		Timeout:  chat.Key("timeout").MustInt(120),
		MaxIter:  chat.Key("agentic_max_iterations").MustInt(20),
		MCPURL:   mcp.Key("url").MustString("http://127.0.0.1:9077"),
		Language: app.Key("language").MustString("en"),

		MultiNumCtx:   chat.Key("context_length").MustInt(0),
		Pool:          map[string]PoolModel{},
		SkillCategory: f.Section("BROWSER").Key("default_skill_category").MustString("skills"),
		CompactKeep:   chat.Key("compact_keep_recent").MustInt(6),
		QAReview:      strings.ToLower(strings.TrimSpace(chat.Key("qa_review").MustString("off"))),
	}

	if alias := strings.TrimSpace(f.Section("MODELS").Key("base").String()); alias != "" {
		if sec := modelSection(f, alias); sec != nil {
			if v := sec.Key("base_url").String(); v != "" {
				cfg.BaseURL = v
			}
			if v := sec.Key("api_key").String(); v != "" {
				cfg.APIKey = v
			}
			if v := sec.Key("model").String(); v != "" {
				cfg.Model = v
			}
		}
	}
	cfg.BaseURL = strings.TrimRight(cfg.BaseURL, "/")

	if models := f.Section("MODELS"); models != nil {
		for _, key := range models.Keys() {
			active := strings.ToLower(strings.TrimSpace(key.String()))
			if active != "1" && active != "true" && active != "yes" && active != "on" {
				continue
			}
			name := key.Name()
			if strings.EqualFold(name, "base") {
				continue
			}
			sec := modelSection(f, name)
			if sec == nil {
				continue
			}

			bu := sec.Key("base_url").String()
			if bu == "" {
				bu = cfg.BaseURL
			}
			ak := sec.Key("api_key").String()
			if ak == "" {
				ak = cfg.APIKey
			}
			md := sec.Key("model").String()
			if md == "" {
				md = cfg.Model
			}
			cfg.Pool[name] = PoolModel{
				Name: name, BaseURL: strings.TrimRight(bu, "/"),
				APIKey: ak, Model: md,
				Profile: strings.TrimSpace(sec.Key("profile").String()),
			}
		}
	}
	if cfg.BaseURL == "" || cfg.Model == "" {
		return cfg, fmt.Errorf("no base model: elect one in the config's model pool ([MODELS] base), or fill [API] base_url and model, in %s", path)
	}
	return cfg, nil
}

func modelSection(f *ini.File, name string) *ini.Section {
	want := "MODEL:" + name
	for _, sec := range f.Sections() {
		if strings.EqualFold(sec.Name(), want) {
			return sec
		}
	}
	return nil
}

func configPath() string {
	if p := os.Getenv("HOPLITA_CONFIG"); p != "" {
		return p
	}
	if runtime.GOOS == "windows" {
		if appdata := os.Getenv("APPDATA"); appdata != "" {
			return filepath.Join(appdata, "hoplita", "config.ini")
		}
	}
	for _, p := range installDirs() {
		p = filepath.Join(p, "config.ini")
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return "config.ini"
}

func installDirs() []string {
	dirs := []string{}
	if exe, err := os.Executable(); err == nil {
		if real, err := filepath.EvalSymlinks(exe); err == nil {
			exe = real
		}
		dirs = append(dirs, filepath.Join(filepath.Dir(exe), "..", ".."))
	}
	if home, err := os.UserHomeDir(); err == nil {
		dirs = append(dirs, filepath.Join(home, ".config", "hoplita"),
			filepath.Join(home, "hoplita"))
	}
	return append(dirs, "/opt/hoplita")
}

type Message struct {
	Role    string      `json:"role"`
	Content interface{} `json:"content,omitempty"`

	Reasoning    string     `json:"reasoning_content,omitempty"`
	ReasoningAlt string     `json:"reasoning,omitempty"`
	ToolCalls    []ToolCall `json:"tool_calls,omitempty"`
	ToolCallID   string     `json:"tool_call_id,omitempty"`
	Name         string     `json:"name,omitempty"`
}

type ToolCall struct {
	ID       string       `json:"id"`
	Type     string       `json:"type"`
	Function FunctionCall `json:"function"`
}

type FunctionCall struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

func httpClient(timeoutS int) *http.Client {
	return &http.Client{
		Timeout: time.Duration(timeoutS) * time.Second,
	}
}

func postJSON(client *http.Client, url string, payload interface{}, headers map[string]string) ([]byte, int, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, 0, err
	}
	req, err := http.NewRequest("POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	r, err := client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer r.Body.Close()
	b, err := io.ReadAll(r.Body)
	return b, r.StatusCode, err
}

func mcpHealth(cfg Config) bool {
	r, err := httpClient(5).Get(cfg.MCPURL + "/health")
	if err != nil {
		return false
	}
	defer r.Body.Close()
	return r.StatusCode == 200
}

func mcpListTools(cfg Config) ([]map[string]interface{}, error) {
	r, err := httpClient(10).Get(cfg.MCPURL + "/tools")
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	var resp struct {
		Tools []map[string]interface{} `json:"tools"`
	}
	if err := json.NewDecoder(r.Body).Decode(&resp); err != nil {
		return nil, err
	}
	return resp.Tools, nil
}

func mcpCallTool(cfg Config, name string, args map[string]interface{}) (interface{}, error) {
	body, status, err := postJSON(httpClient(cfg.Timeout), cfg.MCPURL+"/call",
		map[string]interface{}{"tool": name, "arguments": args}, nil)
	if err != nil {
		return nil, err
	}
	if status != 200 {
		return nil, fmt.Errorf("/call HTTP %d: %s", status, truncate(string(body), 200))
	}
	var resp map[string]interface{}
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, err
	}
	return resp["result"], nil
}

func mcpFeedbackAsync(cfg Config, goal, response string, durationS float64, ok bool) {
	go func() {
		outcome := "fail"
		if ok {
			outcome = "ok"
		}
		_, _, _ = postJSON(httpClient(5), cfg.MCPURL+"/feedback",
			map[string]interface{}{
				"goal":       goal,
				"result":     response,
				"steps":      []interface{}{},
				"feedback":   outcome,
				"duration_s": durationS,
				"tags":       []string{"auto_go", cfg.Model},
			}, nil)
	}()
}

func expandHome(p string) string {
	if strings.HasPrefix(p, "~") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, p[1:])
		}
	}
	return p
}

func f_browser_skill_cat(cfg Config) string {
	if cfg.SkillCategory != "" {
		return cfg.SkillCategory
	}
	return "skills"
}

func resolveModelByName(cfg Config, name string) (string, string, string) {
	if name != "" && name != "base" && name != "default" {
		if m, ok := cfg.Pool[name]; ok {
			return m.BaseURL, m.APIKey, m.Model
		}
	}
	return cfg.BaseURL, cfg.APIKey, cfg.Model
}

func cfgWithModel(cfg Config, name string) Config {
	bu, ak, md := resolveModelByName(cfg, name)
	c := cfg
	c.BaseURL, c.APIKey, c.Model = bu, ak, md
	return c
}

type pinnedSkill struct {
	Title   string
	Content string
}

var pinnedSkills []pinnedSkill

var sessionGoal string

var sessionProject string

var sessionQA string

func agenticCall(cfg Config, history []Message, userMsg string, verify string) (string, error) {
	t0 := time.Now()

	messages := make([]Message, 0, len(history)+1)
	messages = append(messages, stripOldRecords(history)...)
	messages = append(messages, Message{Role: "user", Content: userMsg})

	finalContent, err := mcpTurn(cfg, messages, verify)
	if err != nil {
		return "", err
	}

	dt := time.Since(t0).Seconds()
	ok := finalContent != "" &&
		!strings.HasPrefix(strings.ToLower(finalContent), "error") &&
		!strings.Contains(finalContent, "⚠")
	mcpFeedbackAsync(cfg, userMsg, finalContent, dt, ok)

	if finalContent == "" {
		return "(the turn ended with no final response)", nil
	}
	return finalContent, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

var convID = fmt.Sprintf("cli-%d", time.Now().UnixNano())

func forgetWorkingMemory(cfg Config) {
	postJSON(httpClient(3), cfg.MCPURL+"/call",
		map[string]interface{}{"tool": "working_memory_forget",
			"arguments": map[string]interface{}{
				"sessions": []string{convID}}}, nil)

	postJSON(httpClient(3), cfg.MCPURL+"/agent/memory_snapshot",
		map[string]interface{}{"session": convID, "action": "reset"}, nil)
}

func showTokens(cfg Config, reset bool) {
	if reset {
		if _, status, err := postJSON(httpClient(5),
			cfg.MCPURL+"/stats/tokens/reset",
			map[string]interface{}{}, nil); err != nil || status != 200 {
			fmt.Println("❌ reset failed")
			return
		}
	}
	r, err := httpClient(5).Get(cfg.MCPURL + "/stats/tokens")
	if err != nil {
		fmt.Println("❌", err)
		return
	}
	defer r.Body.Close()
	var resp struct {
		Since  string `json:"since"`
		Total  int64  `json:"total"`
		Models []struct {
			Model      string `json:"model"`
			Name       string `json:"name"`
			Sent       string `json:"sent"`
			Calls      int64  `json:"calls"`
			Prompt     int64  `json:"prompt"`
			Completion int64  `json:"completion"`
			Total      int64  `json:"total"`
		} `json:"models"`
	}
	json.NewDecoder(r.Body).Decode(&resp)
	if resp.Since != "" {
		fmt.Printf("Counting since %s\n", truncate(resp.Since, 16))
	}
	if len(resp.Models) == 0 {
		fmt.Println("No tokens counted since the last reset.")
		return
	}
	fmt.Printf("  %-36s %10s %12s %12s %12s\n",
		"model", "calls", "in", "out", "total")
	for _, m := range resp.Models {
		fmt.Printf("  %-36s %10d %12d %12d %12d\n",
			truncate(meterName(m.Name, m.Sent, m.Model), 36), m.Calls, m.Prompt, m.Completion, m.Total)
	}
	fmt.Printf("  %-36s %10s %12s %12s %12d\n", "TOTAL", "", "", "", resp.Total)
}

func meterName(name, sent, model string) string {
	name, sent = strings.TrimSpace(name), strings.TrimSpace(sent)
	if sent == "" && !strings.ContainsAny(model, "/\\") {
		sent = strings.TrimSpace(model)
	}
	if name != "" && sent != "" && !strings.EqualFold(name, sent) {
		return name + " · " + sent
	}
	if name != "" {
		return name
	}
	if sent != "" {
		return sent
	}
	m := strings.TrimRight(strings.ReplaceAll(model, "\\", "/"), "/")
	if i := strings.LastIndex(m, "/"); i >= 0 {
		m = m[i+1:]
	}
	for _, ext := range []string{".gguf", ".bin", ".safetensors"} {
		if strings.HasSuffix(strings.ToLower(m), ext) {
			m = m[:len(m)-len(ext)]
		}
	}
	if m == "" {
		return "?"
	}
	return m
}

func showHelp() {
	fmt.Println(`Commands:
  /help      this help
  /info      config and MCP status
  /tools     list the MCP's available tools
  /facts     list the user's accumulated facts
  /lessons   list the accumulated lessons
  /goal      set this session's acceptance bar (/goal off clears it)
  /project   open a project for this session (/project off closes it)
  /qa        this session's judge: a pool model, off, or auto (config)
  /model     pin this session's model: a pool name, or base (bare = show pool)
  /skill     skills: add <path.md> | add <name>: <text> | pin/unpin/pinned
  /tokens    what each model has eaten (accumulated; /tokens reset zeroes it)
  /context   what the next prompt weighs, block by block, heaviest first
  /compact   fold the old part of this session's context into a summary now
  /memory    the weekly pass: pending proposals + what it applied
             (/memory run | ok <id> | no <id> | undo <id>)
  /clear     clear the screen
  /quit      exit

Anything else goes to the LLM in agentic mode.
The LLM has full access to the MCP's tools — to invoke a skill,
search notes, browse the web, add a fact, etc., just ask it
in natural language ("remember that my cat is called Pixel").`)
}

func showInfo(cfg Config, tools []map[string]interface{}) {
	fmt.Printf("API:        %s @ %s\n", cfg.Model, cfg.BaseURL)
	fmt.Printf("MCP:        %s (%d tools)\n", cfg.MCPURL, len(tools))
	fmt.Printf("Lang:       %s\n", cfg.Language)
	fmt.Printf("Max iter:   %d   Timeout: %ds\n", cfg.MaxIter, cfg.Timeout)
	fmt.Printf("Context:    %d tokens\n", cfg.MultiNumCtx)
}

func showTools(tools []map[string]interface{}) {
	for _, t := range tools {
		fn, _ := t["function"].(map[string]interface{})
		name, _ := fn["name"].(string)
		desc, _ := fn["description"].(string)
		fmt.Printf("  %-32s %s\n", name, truncate(desc, 80))
	}
}

func showFacts(cfg Config) {
	r, err := httpClient(5).Get(cfg.MCPURL + "/facts/list")
	if err != nil {
		fmt.Println("❌", err)
		return
	}
	defer r.Body.Close()
	var resp struct {
		Facts []map[string]interface{} `json:"facts"`
		Total int                      `json:"total"`
	}
	json.NewDecoder(r.Body).Decode(&resp)
	fmt.Printf("Total facts: %d\n", resp.Total)
	for _, f := range resp.Facts {
		text, _ := f["text"].(string)
		ts, _ := f["ts"].(string)
		fmt.Printf("  %s  %s\n", truncate(ts, 19), text)
	}
}

func showLessons(cfg Config) {
	r, err := httpClient(5).Get(cfg.MCPURL + "/lessons/list")
	if err != nil {
		fmt.Println("❌", err)
		return
	}
	defer r.Body.Close()
	var resp struct {
		Lessons []map[string]interface{} `json:"lessons"`
		Active  int                      `json:"active"`
		Total   int                      `json:"total"`
	}
	json.NewDecoder(r.Body).Decode(&resp)
	fmt.Printf("Lessons: %d active / %d total\n", resp.Active, resp.Total)
	for _, l := range resp.Lessons {
		text, _ := l["text"].(string)
		conf, _ := l["confidence"].(float64)
		stale, _ := l["stale"].(bool)
		marker := " "
		if stale {
			marker = "✗"
		}
		fmt.Printf("  %s  (%.0f%%)  %s\n", marker, conf*100, truncate(text, 80))
	}
}

var (
	taskRunning int32
	stopFlag    int32
)

func taskBegin()    { atomic.StoreInt32(&stopFlag, 0); atomic.StoreInt32(&taskRunning, 1) }
func taskEnd()      { atomic.StoreInt32(&taskRunning, 0); atomic.StoreInt32(&stopFlag, 0) }
func stopped() bool { return atomic.LoadInt32(&stopFlag) == 1 }

var lastPromptTokens int

var firstPromptTokens int

func fmtTokens(n int) string {
	if n >= 1000 {
		return fmt.Sprintf("%.1fk", float64(n)/1000.0)
	}
	return fmt.Sprintf("%d", n)
}

func renderTokenMeter(cfg Config, promptTokens int) {
	limit := cfg.MultiNumCtx
	if limit <= 0 {
		limit = 20000
	}
	pct := float64(promptTokens) / float64(limit)
	var color string
	switch {
	case pct >= 0.9:
		color = "\033[31m"
	case pct >= 0.7:
		color = "\033[33m"
	default:
		color = "\033[32m"
	}
	reset := "\033[0m"
	bar := ""
	filled := int(pct * 10)
	for i := 0; i < 10; i++ {
		if i < filled {
			bar += "█"
		} else {
			bar += "░"
		}
	}
	fmt.Printf("%s[%s] %s/%s (%.0f%%)%s\n",
		color, bar, fmtTokens(promptTokens), fmtTokens(limit), pct*100, reset)
}

func mcpContextLimit(cfg Config) int {
	url := cfg.MCPURL + "/agent/context_limit"
	if cfg.modelFixed {
		url += "?base_url=" + neturl.QueryEscape(cfg.BaseURL) +
			"&api_key=" + neturl.QueryEscape(cfg.APIKey) +
			"&model=" + neturl.QueryEscape(cfg.Model)
	}
	r, err := httpClient(15).Get(url)
	if err != nil {
		return 0
	}
	defer r.Body.Close()
	var out struct {
		Limit int `json:"limit"`
	}
	if json.NewDecoder(r.Body).Decode(&out) != nil {
		return 0
	}
	return out.Limit
}

func setupSignalHandler() {
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, os.Interrupt, syscall.SIGINT)
	go func() {
		for range ch {
			if atomic.LoadInt32(&taskRunning) == 1 {

				atomic.StoreInt32(&stopFlag, 1)
				fmt.Println("\n⏹ Stopping the task… (Ctrl-C again at the prompt to exit)")
			} else {

				fmt.Println("\n👋 Exiting.")
				os.Exit(0)
			}
		}
	}()
}

func runSpawn(cfg Config, serverURL, token, handle string) int {
	if token == "" {
		fmt.Fprintln(os.Stderr, "❌ --spawn requires --token <TOKEN>")
		return 1
	}
	if serverURL == "" {
		fmt.Fprintln(os.Stderr, "❌ --spawn requires --server <URL> (the owner's federation server)")
		return 1
	}
	if handle == "" {

		if h, err := os.Hostname(); err == nil && h != "" {
			handle = strings.ToLower(h)
		} else {
			fmt.Fprintln(os.Stderr, "❌ --spawn requires --handle <NAME> (could not derive from hostname)")
			return 1
		}
	}
	if !mcpHealth(cfg) {
		fmt.Fprintf(os.Stderr, "❌ The MCP is not responding at %s\n", cfg.MCPURL)
		fmt.Fprintln(os.Stderr, "   Start the MCP first (with the child's vault password), then relaunch --spawn.")
		return 1
	}
	payload := map[string]interface{}{
		"server_url": serverURL, "token": token, "handle": handle,
	}
	body, status, err := postJSON(httpClient(30), cfg.MCPURL+"/spawn/enroll", payload, nil)
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ Could not contact the MCP: %v\n", err)
		return 1
	}
	var resp map[string]interface{}
	_ = json.Unmarshal(body, &resp)
	if status != 200 || resp["error"] != nil {
		fmt.Fprintf(os.Stderr, "❌ Enrollment failed: %v\n", resp["error"])
		return 1
	}
	fmt.Printf("✓ Child '%v' enrolled under '%v'.\n", resp["handle"], resp["owner"])
	fmt.Println("  Identity and session token saved in the MCP's vault.")
	fmt.Println("  This agent now appears in the owner's GUI. Start the MCP normally to make it operational.")
	return 0
}

func main() {

	var spawnMode bool
	var spawnServer, spawnToken, spawnHandle string

	var one oneShotOpts
	var freeArgs []string
	args := os.Args[1:]
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--json":
			one.asJSON = true
		case "--quiet", "-q":
			one.quiet = true
		case "--verify":
			if i+1 < len(args) {
				i++
				one.verify = args[i]
			}
		case "--yes", "-y":
			autoApprovePlans = true
		case "--help", "-h":
			printUsage()
			os.Exit(0)
		case "--spawn":
			spawnMode = true
		case "--server":
			if i+1 < len(args) {
				i++
				spawnServer = args[i]
			}
		case "--token":
			if i+1 < len(args) {
				i++
				spawnToken = args[i]
			}
		case "--handle":
			if i+1 < len(args) {
				i++
				spawnHandle = args[i]
			}
		default:
			if !strings.HasPrefix(args[i], "-") {
				freeArgs = append(freeArgs, args[i])
			}
		}
	}

	planAckInteractive = !stdinIsPipe()

	cfg, err := loadConfig(configPath())
	if err != nil {
		fmt.Fprintln(os.Stderr, "❌", err)
		os.Exit(1)
	}

	if spawnMode {
		os.Exit(runSpawn(cfg, spawnServer, spawnToken, spawnHandle))
	}

	if piped := readStdin(2 << 20); piped != "" || len(freeArgs) > 0 {

		loadI18n(cfg.Language)
		one.prompt = buildOneShotPrompt(strings.Join(freeArgs, " "), piped)

		os.Exit(runOneShot(cfg, one))
	}

	if !mcpHealth(cfg) {
		fmt.Fprintf(os.Stderr, "❌ MCP is not responding at %s\n", cfg.MCPURL)
		fmt.Fprintln(os.Stderr, "   Start it with: python hoplita-mcp.py --port 9077")
		os.Exit(1)
	}
	tools, err := mcpListTools(cfg)
	if err != nil {
		fmt.Fprintln(os.Stderr, "⚠ No tools:", err)
		tools = nil
	}

	loadI18n(cfg.Language)

	if cfg.MultiNumCtx <= 0 {
		if n := mcpContextLimit(cfg); n > 0 {
			cfg.MultiNumCtx = n
			fmt.Printf("✓ context_length auto-detected: %d\n", n)
		} else {
			cfg.MultiNumCtx = 20000
		}
	}

	fmt.Printf("✓ HOPLITA CLI v%s — %s @ %s, %d tool(s)\n", HOPLITAVersion, cfg.Model, cfg.BaseURL, len(tools))
	fmt.Println("  /help commands · /quit exit · Ctrl-C for the task in progress")
	fmt.Println()

	setupSignalHandler()

	in := bufio.NewReader(os.Stdin)
	var sessionHistory []Message
	lastExtracted := 0
	const extractEveryN = 10

	baseCfg := cfg
	chatModelOverride := ""

	extractPending := func() {
		segment := sessionHistory[lastExtracted:]
		hasUser := false
		for _, m := range segment {
			if m.Role == "user" {
				hasUser = true
				break
			}
		}
		if !hasUser {
			return
		}
		lastExtracted = len(sessionHistory)
		mcpDistill(cfg, segment)
	}

	for {
		fmt.Print("> ")
		line, err := in.ReadString('\n')
		if err != nil {
			fmt.Println()
			extractPending()
			forgetWorkingMemory(cfg)
			return
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		switch {
		case line == "/quit", line == "/q", line == "/exit":
			extractPending()
			forgetWorkingMemory(cfg)
			return
		case line == "/tokens", line == "/tokens reset":
			showTokens(cfg, line == "/tokens reset")
		case line == "/compact":

			sessionHistory = mcpCompact(cfg, sessionHistory)
		case line == "/context":
			showContextMap(cfg, sessionHistory)
		case line == "/memory" || strings.HasPrefix(line, "/memory "):

			arg := strings.TrimSpace(strings.TrimPrefix(line, "/memory"))
			parts := strings.Fields(arg)
			switch {
			case arg == "":
				memoryShow(cfg)
			case arg == "run":
				memoryRun(cfg)
			case len(parts) == 2 && (parts[0] == "ok" || parts[0] == "no"):
				memoryDecide(cfg, parts[1], parts[0] == "ok")
			case len(parts) == 2 && parts[0] == "undo":
				memoryUndo(cfg, parts[1])
			default:
				fmt.Println("usage: /memory | /memory run | /memory ok <id> " +
					"| /memory no <id> | /memory undo <id>")
			}
		case line == "/help", line == "/?":
			showHelp()
		case line == "/info":
			showInfo(cfg, tools)
		case line == "/tools":
			showTools(tools)
		case line == "/facts":
			showFacts(cfg)
		case line == "/lessons":
			showLessons(cfg)
		case line == "/clear":
			fmt.Print("\033[2J\033[H")
			extractPending()
		case line == "/goal" || strings.HasPrefix(line, "/goal "):

			arg := strings.TrimSpace(strings.TrimPrefix(line, "/goal"))
			switch strings.ToLower(arg) {
			case "":
				if sessionGoal == "" {
					fmt.Println(tr("goal.none"))
				} else {
					fmt.Println(tr("goal.current", "goal", sessionGoal))
				}
			case "off", "no", "clear", "none":
				sessionGoal = ""
				fmt.Println(tr("goal.cleared"))
			default:
				sessionGoal = arg
				fmt.Println(tr("goal.set", "goal", arg))
			}
		case line == "/project" || strings.HasPrefix(line, "/project "):

			arg := strings.TrimSpace(strings.TrimPrefix(line, "/project"))
			switch strings.ToLower(arg) {
			case "":
				if sessionProject == "" {
					fmt.Println(tr("project.none"))
				} else {
					fmt.Println(tr("project.current", "path", sessionProject))
				}
			case "off", "no", "clear", "none", "close":
				sessionProject = ""
				fmt.Println(tr("project.cleared"))
			default:
				if st, err := os.Stat(arg); err != nil || !st.IsDir() {
					fmt.Println(tr("project.bad", "path", arg))
					break
				}
				if abs, err := filepath.Abs(arg); err == nil {
					arg = abs
				}
				sessionProject = arg
				fmt.Println(tr("project.set", "path", arg))
			}
		case line == "/qa" || strings.HasPrefix(line, "/qa "):

			arg := strings.ToLower(strings.TrimSpace(
				strings.TrimPrefix(line, "/qa")))
			switch arg {
			case "":
				cur := sessionQA
				if cur == "" {
					cur = tr("qa.config_default")
				}
				fmt.Println(tr("qa.active", "name", cur))
			case "auto", "base", "default", "config":
				sessionQA = ""
				fmt.Println(tr("qa.cleared"))
			case "off", "no", "none":
				sessionQA = "off"
				fmt.Println(tr("qa.off"))
			default:
				if m, okp := cfg.Pool[arg]; okp {
					sessionQA = arg
					fmt.Println(tr("qa.set", "name", arg, "model", m.Model))
				} else {
					names := make([]string, 0, len(cfg.Pool))
					for n := range cfg.Pool {
						names = append(names, n)
					}
					fmt.Println(tr("qa.unknown", "name", arg,
						"pool", strings.Join(names, ", ")))
				}
			}
		case line == "/skill" || strings.HasPrefix(line, "/skill "):
			arg := strings.TrimSpace(strings.TrimPrefix(line, "/skill"))
			cat := f_browser_skill_cat(cfg)
			low := strings.ToLower(arg)

			if low == "pinned" || low == "pins" {
				if len(pinnedSkills) == 0 {
					fmt.Println(tr("skill.none_pinned"))
				} else {
					fmt.Println(tr("skill.pinned_header", "n", fmt.Sprintf("%d", len(pinnedSkills))))
					for _, s := range pinnedSkills {
						fmt.Printf("  📌 %s (%d chars)\n", s.Title, len(s.Content))
					}
				}
				break
			}
			if strings.HasPrefix(low, "pin ") || low == "pin" {
				term := strings.TrimSpace(arg[3:])
				if term == "" {
					fmt.Println(tr("skill.pin_usage"))
					break
				}

				var s *pinnedSkill
				body, st, _ := postJSON(httpClient(4), cfg.MCPURL+"/notes/search",
					map[string]interface{}{"query": term, "category": cat, "n_results": 1}, nil)
				if st == 200 {
					var resp struct {
						Results []struct {
							Title, Content string
						} `json:"results"`
					}
					if json.Unmarshal(body, &resp) == nil && len(resp.Results) > 0 && resp.Results[0].Content != "" {
						s = &pinnedSkill{Title: resp.Results[0].Title, Content: resp.Results[0].Content}
					}
				}
				if s == nil {
					fmt.Printf("❌ %s\n", tr("skill.not_found_pin", "term", term))
					break
				}
				already := false
				for _, p := range pinnedSkills {
					if p.Title == s.Title {
						already = true
						break
					}
				}
				if already {
					fmt.Printf("📌 %s\n", tr("skill.already_pinned", "title", s.Title))
				} else {
					pinnedSkills = append(pinnedSkills, *s)
					fmt.Printf("📌 %s\n", tr("skill.pinned", "title", s.Title, "n", fmt.Sprintf("%d", len(pinnedSkills))))
				}
				break
			}
			if strings.HasPrefix(low, "unpin ") || low == "unpin" {
				term := strings.TrimSpace(arg[5:])
				if strings.ToLower(term) == "all" {
					n := len(pinnedSkills)
					pinnedSkills = nil
					fmt.Printf("📌 %s\n", tr("skill.unpinned_all", "n", fmt.Sprintf("%d", n)))
					break
				}
				before := len(pinnedSkills)
				var kept []pinnedSkill
				for _, p := range pinnedSkills {
					if !strings.EqualFold(p.Title, term) {
						kept = append(kept, p)
					}
				}
				pinnedSkills = kept
				if len(pinnedSkills) < before {
					fmt.Printf("📌 %s\n", tr("skill.unpinned", "title", term))
				} else {
					fmt.Printf("❌ %s\n", tr("skill.not_pinned", "title", term))
				}
				break
			}

			if !strings.HasPrefix(low, "add") {
				fmt.Println(tr("skill.add_usage"))
				break
			}
			body := strings.TrimSpace(arg[3:])
			var title, content string
			if body == "" {
				fmt.Println(tr("skill.add_usage"))
				break
			}

			if fi, err := os.Stat(expandHome(body)); err == nil && !fi.IsDir() {
				data, rerr := os.ReadFile(expandHome(body))
				if rerr != nil {
					fmt.Printf("❌ %s\n", tr("skill.read_err", "path", body, "e", rerr.Error()))
					break
				}
				content = string(data)
				base := filepath.Base(expandHome(body))
				title = strings.TrimSuffix(base, filepath.Ext(base))
			} else if i := strings.Index(body, ":"); i > 0 {
				title = strings.TrimSpace(body[:i])
				content = strings.TrimSpace(body[i+1:])
			} else {
				fmt.Println(tr("skill.add_usage"))
				break
			}
			if title == "" || content == "" {
				fmt.Println(tr("skill.add_usage"))
				break
			}
			_, aerr := mcpCallTool(cfg, "add_note", map[string]interface{}{
				"category": cat, "title": title, "content": content,
			})
			if aerr != nil {
				fmt.Printf("❌ %s\n", tr("skill.save_err", "e", aerr.Error()))
			} else {
				fmt.Printf("✅ %s\n", tr("skill.added", "title", title, "cat", cat,
					"n", fmt.Sprintf("%d", len(content))))
			}
		case line == "/model" || strings.HasPrefix(line, "/model "):
			arg := strings.TrimSpace(strings.TrimPrefix(line, "/model"))
			if arg == "" {
				active := chatModelOverride
				if active == "" {
					active = "base"
				}
				fmt.Printf("🧠 %s\n", tr("model.active", "name", active))
				fmt.Printf("   base [API] = %s\n", cfg.Model)
				if len(cfg.Pool) > 0 {
					fmt.Println(tr("model.pool_list"))
					for name, m := range cfg.Pool {
						fmt.Printf("  • %s → %s\n", name, m.Model)
					}
				} else {
					fmt.Println(tr("model.no_pool"))
				}
			} else if arg == "base" || arg == "default" {
				chatModelOverride = ""
				fmt.Printf("🧠 %s\n", tr("model.switched_base", "model", baseCfg.Model))
				cfg = baseCfg
			} else if m, ok := baseCfg.Pool[arg]; ok {
				chatModelOverride = arg
				cfg = cfgWithModel(baseCfg, arg)
				cfg.modelFixed = true
				fmt.Printf("🧠 %s\n", tr("model.switched", "name", arg, "model", m.Model))
			} else {
				names := make([]string, 0, len(baseCfg.Pool))
				for n := range baseCfg.Pool {
					names = append(names, n)
				}
				avail := "—"
				if len(names) > 0 {
					avail = strings.Join(names, ", ")
				}
				fmt.Printf("❌ %s\n", tr("model.unknown", "name", arg, "avail", avail))
			}
		default:
			var answer string
			var err error
			taskBegin()

			answer, err = agenticCall(cfg, sessionHistory, line, "")
			wasStopped := stopped()
			taskEnd()
			if err != nil {
				fmt.Println("❌", err)

				sessionHistory = append(sessionHistory,
					Message{Role: "user", Content: line})
				if note := turnTraceNote(err.Error()); note != "" {

					sessionHistory = append(sessionHistory,
						Message{Role: "assistant",
							Content: "[Turn FAILED: " + truncate(err.Error(), 200) +
								"]\n\n" + note})
				}
			} else {
				fmt.Println()
				fmt.Println(answer)
				fmt.Println()
				userContent := line
				assistantContent := answer
				if wasStopped {

					assistantContent = "[The user STOPPED this task before it " +
						"completed. Partial result:]\n" + answer
				}
				var userStored interface{} = userContent
				if lastUserSent != nil && !wasStopped {

					userStored = lastUserSent
				}
				lastUserSent = nil
				sessionHistory = append(sessionHistory,
					Message{Role: "user", Content: userStored})

				if len(lastTranscript) > 0 {
					sessionHistory = append(sessionHistory, lastTranscript...)
				} else if note := turnTraceNote(""); note != "" {
					assistantContent = assistantContent + "\n\n" + note
				}
				sessionHistory = append(sessionHistory,
					Message{Role: "assistant", Content: assistantContent})

				if lastPromptTokens > 0 {
					renderTokenMeter(cfg, lastPromptTokens)
				}

				limit := cfg.MultiNumCtx
				if limit <= 0 {
					limit = 20000
				}
				measured := firstPromptTokens
				if measured == 0 {
					measured = lastPromptTokens
				}
				if float64(measured)/float64(limit) >= 0.85 {
					sessionHistory = mcpCompact(cfg, sessionHistory)
					lastExtracted = 0
				}
				if len(sessionHistory)-lastExtracted >= extractEveryN {
					extractPending()
				}
			}
		}
	}
}
