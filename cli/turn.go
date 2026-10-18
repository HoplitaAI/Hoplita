package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

func pinnedSkillsPayload() []map[string]string {
	out := make([]map[string]string, 0, len(pinnedSkills))
	for _, s := range pinnedSkills {
		out = append(out, map[string]string{"title": s.Title, "content": s.Content})
	}
	return out
}

type turnStep struct{ action, detail, result string }

type turnQA struct {
	ok  bool
	why string
}

var lastTurnSteps []turnStep
var lastTurnQA []turnQA

var lastTurnRecord string

var lastTranscript []Message

var lastUserSent interface{}

var recordSentinels = []string{
	"[REGISTRO INTERNO DEL TURNO", "[INTERNAL TURN RECORD",
	"[Lo que hice en el turno anterior", "[What I did in the previous turn",
}

func stripOldRecords(history []Message) []Message {
	lastA := -1
	for i := len(history) - 1; i >= 0; i-- {
		if history[i].Role == "assistant" {
			lastA = i
			break
		}
	}
	out := make([]Message, len(history))
	copy(out, history)
	for i := range out {
		if i == lastA || out[i].Role != "assistant" {
			continue
		}
		c, okc := out[i].Content.(string)
		if !okc {
			continue
		}
		for _, s := range recordSentinels {
			if j := strings.Index(c, s); j >= 0 {
				out[i].Content = strings.TrimRight(c[:j], " \n")
				break
			}
		}
	}
	return out
}

func cutRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}

func turnTraceNote(failed string) string {
	if lastTurnRecord != "" {
		return lastTurnRecord
	}
	if len(lastTurnSteps) == 0 && len(lastTurnQA) == 0 {
		return ""
	}
	lines := []string{}
	max := len(lastTurnSteps)
	if max > 8 {
		max = 8
	}
	for _, p := range lastTurnSteps[:max] {
		r := strings.TrimSpace(strings.ReplaceAll(p.result, "\n", " "))
		line := "  " + p.action + "(" + cutRunes(p.detail, 90) + ")"
		if r != "" {
			line += " → " + cutRunes(r, 220)

			if len([]rune(r)) >= 200 {
				line += tr("chat.turn_trace_cut")
			}
		}
		lines = append(lines, line)
	}
	if len(lastTurnSteps) > 8 {
		lines = append(lines, tr("chat.turn_trace_more", "n",
			fmt.Sprintf("%d", len(lastTurnSteps)-8)))
	}
	for _, v := range lastTurnQA {
		if !v.ok {
			lines = append(lines, tr("chat.turn_qa_fail", "why", v.why))
		}
	}
	if len(lastTurnQA) > 0 && lastTurnQA[len(lastTurnQA)-1].ok {
		lines = append(lines, tr("chat.turn_qa_ok"))
	}
	if failed != "" {
		lines = append(lines, tr("chat.turn_trace_failed", "e",
			cutRunes(failed, 200)))
	}
	note := tr("chat.turn_trace", "steps", strings.Join(lines, "\n"))
	if note == "chat.turn_trace" {

		note = "[Previous turn:\n" + strings.Join(lines, "\n") + "]"
	}
	return note
}

func mcpTurn(cfg Config, messages []Message, verify string) (string, error) {

	firstPromptTokens = 0

	qaMode := cfg.QAReview
	if qaMode == "" {
		qaMode = "off"
	}
	if sessionQA == "off" {
		qaMode = "off"
	} else if sessionQA != "" && qaMode == "off" {
		qaMode = "auto"
	}
	payload := map[string]interface{}{
		"messages":       messages,
		"max_iterations": cfg.MaxIter,

		"qa": qaMode,

		"ack_mode":   planAckMode(),
		"unattended": planAckMode() == "",

		"delegate": []string{},

		"inject":        true,
		"pinned_skills": pinnedSkillsPayload(),

		"session": convID,
	}

	if cfg.modelFixed {
		payload["endpoint"] = map[string]string{
			"base_url": cfg.BaseURL, "api_key": cfg.APIKey, "model": cfg.Model,
		}
	}
	if strings.TrimSpace(verify) != "" {
		payload["verify"] = verify
	}
	if strings.TrimSpace(sessionGoal) != "" {

		payload["goal"] = sessionGoal
	}
	if strings.TrimSpace(sessionProject) != "" {

		payload["code_project"] = sessionProject
	}
	if sessionQA != "" && sessionQA != "off" {
		if m, okp := cfg.Pool[sessionQA]; okp {

			payload["qa_endpoint"] = map[string]string{
				"base_url": m.BaseURL, "api_key": m.APIKey,
				"model": m.Model,
			}
		}
	}

	lastTurnSteps, lastTurnQA, lastTurnRecord = nil, nil, ""

	body, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	req, err := http.NewRequest("POST", cfg.MCPURL+"/agent/turn",
		strings.NewReader(string(body)))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("/agent/turn HTTP %d", resp.StatusCode)
	}
	idleMax := time.Duration(cfg.Timeout+120) * time.Second
	var lastMu sync.Mutex
	last := time.Now()
	touch := func() { lastMu.Lock(); last = time.Now(); lastMu.Unlock() }
	watchStop := make(chan struct{})
	defer close(watchStop)
	go func() {
		tk := time.NewTicker(15 * time.Second)
		defer tk.Stop()
		for {
			select {
			case <-watchStop:
				return
			case <-tk.C:
				lastMu.Lock()
				idle := time.Since(last)
				lastMu.Unlock()
				if idle > idleMax {

					resp.Body.Close()
					return
				}
			}
		}
	}()

	turnID, stopSent, toolIdx := "", false, 0
	sendStop := func() {
		if stopSent || turnID == "" {
			return
		}
		if _, status, err := postJSON(httpClient(5), cfg.MCPURL+"/agent/stop",
			map[string]interface{}{"turn_id": turnID}, nil); err == nil && status == 200 {
			stopSent = true
		}
	}

	sc := bufio.NewScanner(resp.Body)

	sc.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)
	for sc.Scan() {
		touch()
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		if stopped() {
			sendStop()
		}
		var ev map[string]interface{}
		if json.Unmarshal([]byte(line), &ev) != nil {
			continue
		}
		str := func(k string) string { s, _ := ev[k].(string); return s }
		num := func(k string) float64 { f, _ := ev[k].(float64); return f }

		switch str("event") {
		case "turn_start":
			turnID = str("turn_id")
			if shelf := int(num("shelf")); shelf > 0 {
				fmt.Printf("🔧 %d tool(s) with schema + %d on the shelf, max %d iteration(s)\n",
					int(num("tools")), shelf, int(num("max_iterations")))
			} else {
				fmt.Printf("🔧 %d tool(s), max %d iteration(s)\n",
					int(num("tools")), int(num("max_iterations")))
			}
		case "iteration":
			toolIdx = 0
			fmt.Printf("🔄 Iteration %d/%d\n", int(num("n")), int(num("max")))
		case "injected":
			fmt.Printf("📨 Received mid-turn (iteration %d): %s\n",
				int(num("n")), str("message"))
		case "max_iterations":
			fmt.Printf("⚙ Iteration budget changed live: %d → %d\n",
				int(num("was")), int(num("max")))
		case "tool_progress":

			pargs := map[string]string{}
			if am, okm := ev["args"].(map[string]interface{}); okm {
				for k, v := range am {
					if sv, oks := v.(string); oks {
						pargs[k] = sv
					}
				}
			}
			flat := make([]string, 0, len(pargs)*2)
			for k, v := range pargs {
				flat = append(flat, k, v)
			}
			fmt.Println("  " + tr(str("key"), flat...))
		case "tokens":
			if pt := int(num("prompt_tokens")); pt > 0 {
				lastPromptTokens = pt

				if it := int(num("iteration")); it <= 1 {
					firstPromptTokens = pt
				}
			}

		case "intent":
			if items, oki := ev["items"].([]interface{}); oki {
				parts := make([]string, 0, len(items))
				for _, it := range items {
					m, okm := it.(map[string]interface{})
					if !okm {
						continue
					}
					g, _ := m["goal"].(string)
					mark := "○"
					switch st, _ := m["status"].(string); st {
					case "done":
						mark = "✓"
					case "failed":
						mark = "✗"
					case "doing":
						mark = "●"
					case "unfinished":
						mark = "⊘"
					}
					parts = append(parts, mark+" "+g)
				}
				fmt.Println("🧭 " + strings.Join(parts, " · "))
			}
		case "intent_left":

			n, _ := ev["n"].(float64)
			goals := []string{}
			if gs, okg := ev["goals"].([]interface{}); okg {
				for _, g := range gs {
					if sg, oks := g.(string); oks {
						goals = append(goals, sg)
					}
				}
			}
			fmt.Println(tr("chat.intent_left", "n", fmt.Sprintf("%d", int(n)),
				"goals", strings.Join(goals, " · ")))
		case "metacog_nudge":
			council := ""
			if c, okc := ev["council"].(bool); okc && c {
				council = tr("chat.metacog_council_offered")
			}
			switch str("reason") {
			case "loop":
				fmt.Println(tr("chat.metacog_loop", "tool", str("tool")) + council)
			case "last":
				fmt.Println(tr("chat.metacog_last"))
			default:
				fmt.Printf("🧭 no real progress (%d sterile tools) — asked to stop and rethink%s\n",
					int(num("sterile_tools")), council)
			}
		case "model_escalated":
			fmt.Println(tr("chat.escalated", "model", str("to")))
		case "escalate_failed":
			fmt.Println(tr("chat.escalate_failed", "why", str("why")))
		case "metacog_close":
			fmt.Println(tr("chat.metacog_close"))
		case "qa_skipped":
			fmt.Println(tr("chat.qa_skipped_self"))
		case "image_described":
			fmt.Println(tr("chat.image_described", "by", str("by"), "to", str("for"),
				"n", fmt.Sprintf("%d", int(num("n")))))
		case "tool_call":
			toolIdx++
			fmt.Printf("  %d. %s(%s)\n", toolIdx, str("tool"),
				truncate(str("args_preview"), 80))
			lastTurnSteps = append(lastTurnSteps, turnStep{
				action: str("tool"), detail: str("args_preview")})
		case "tool_result":
			fmt.Printf("     ✅ %s\n", truncate(str("result_preview"), 100))

			for i := len(lastTurnSteps) - 1; i >= 0; i-- {
				if lastTurnSteps[i].result == "" {
					lastTurnSteps[i].result = str("result_preview")
					break
				}
			}

		case "verify_run":
			fmt.Fprintf(os.Stderr, "🎯 `%s` (attempt %d)\n", str("command"),
				int(num("attempt")))
		case "verify_result":
			if blocked, _ := ev["blocked"].(bool); blocked {

				fmt.Fprintf(os.Stderr, "🎯 could not run: %s\n",
					truncate(str("output_preview"), 160))
			} else if ok, _ := ev["ok"].(bool); ok {
				fmt.Fprintln(os.Stderr, "🎯 green")
			} else {
				fmt.Fprintf(os.Stderr, "🎯 still red: %s\n",
					truncate(str("output_preview"), 160))
			}
		case "resume_check":

			fmt.Fprintln(os.Stderr, "↩ back to the original request")
		case "context_full":
			fmt.Fprintf(os.Stderr, "⚠ context full: %d prompt tokens, nothing "+
				"could be written — reset or compact\n", int(num("prompt_tokens")))
		case "qa_run":
			fmt.Fprintf(os.Stderr, "🔍 quality review (round %d)\n", int(num("round")))
		case "qa_result":
			if ok, _ := ev["ok"].(bool); ok {
				fmt.Fprintln(os.Stderr, "🔍 passed")
				lastTurnQA = append(lastTurnQA, turnQA{ok: true})
			} else {
				fmt.Fprintf(os.Stderr, "🔍 rejected: %s\n", str("why"))
				lastTurnQA = append(lastTurnQA,
					turnQA{ok: false, why: cutRunes(str("why"), 300)})
			}

		case "prep":
			if n, ok := ev["note"].(map[string]interface{}); ok {
				key, _ := n["key"].(string)
				args := []string{}
				if a, ok := n["args"].(map[string]interface{}); ok {
					for k, v := range a {
						args = append(args, k, fmt.Sprint(v))
					}
				}
				fmt.Println(tr(key, args...))
			}

		case "orch":
			printOrchEvent(ev)

		case "hands_begin":
			fmt.Println(tr("hands.begin"))
		case "hands_action":
			mark := "·"
			if v, ok := ev["verified"].(bool); ok {
				if v {
					mark = "✔"
				} else {
					mark = "✖"
				}
			}
			fmt.Println(tr("hands.action_line", "mark", mark,
				"tool", strings.TrimPrefix(str("tool"), "hands_"),
				"target", str("target")))
		case "hands_end":
			fmt.Println(tr("hands.end", "n", fmt.Sprint(int(num("actions")))))
		case "hands_consent":
			answerHandsConsent(cfg, str("ack_id"), str("model"), str("host"))
		case "config_consent":
			answerConfigConsent(cfg, ev)
		case "config_changed":
			var what []string
			if raw, ok := ev["changes"].([]interface{}); ok {
				for _, x := range raw {
					if c, ok := x.(map[string]interface{}); ok {
						sct, _ := c["section"].(string)
						k, _ := c["key"].(string)
						what = append(what, "["+sct+"] "+k)
					}
				}
			}
			fmt.Println(tr("selfcfg.changed", "what", strings.Join(what, ", ")))
		case "plan_ack":

			subs := []map[string]interface{}{}
			if raw, ok := ev["subtasks"].([]interface{}); ok {
				for _, s := range raw {
					if m, ok := s.(map[string]interface{}); ok {
						subs = append(subs, m)
					}
				}
			}
			answerPlanAck(cfg, str("ack_id"), subs)

		case "reply":
			lastTurnRecord = str("turn_record")
			lastUserSent = ev["last_user_sent"]
			lastTranscript = nil
			if raw, ok := ev["transcript"].([]interface{}); ok && len(raw) > 0 {

				if b, err := json.Marshal(raw); err == nil {
					var msgs []Message
					if json.Unmarshal(b, &msgs) == nil {
						lastTranscript = msgs
					}
				}
			}
			return str("reply"), nil
		case "cancelled":
			lastTurnRecord = str("turn_record")
			return "[The user stopped the task before completing it.]", nil
		case "iteration_limit":
			lastTurnRecord = str("turn_record")
			return fmt.Sprintf("(the loop ran out of iterations after %d rounds "+
				"without concluding)", int(num("iterations"))), nil
		case "error":
			return "", fmt.Errorf("MCP loop: %s", str("error"))
		}
	}
	if err := sc.Err(); err != nil {
		return "", err
	}
	return "", fmt.Errorf("the /agent/turn stream closed with no terminal event")
}
