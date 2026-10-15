package main

import (
	"fmt"
	"os"
	"strings"
)

var (
	autoApprovePlans bool

	planAckInteractive bool
)

func planAckMode() string {
	if autoApprovePlans || planAckInteractive {
		return "stream"
	}
	return ""
}

var lastStep = map[string]string{}

func printOrchEvent(ev map[string]interface{}) {
	if s, _ := ev["event"].(string); s != "orch" {
		return
	}
	o, _ := ev["orch"].(map[string]interface{})
	if o == nil {
		return
	}
	str := func(k string) string { s, _ := o[k].(string); return s }
	switch str("type") {
	case "tick", "snapshot":
		return
	case "note":
		args := []string{}
		if a, ok := o["args"].(map[string]interface{}); ok {
			for k, v := range a {
				args = append(args, k, fmt.Sprint(v))
			}
		}
		fmt.Println(tr(str("key"), args...))
	case "plan":
		subs, _ := o["subtasks"].([]interface{})
		fmt.Printf("🧩 %s\n", tr("msg.plan_subtasks", "n", fmt.Sprint(len(subs))))
		for _, s := range subs {
			fmt.Println("   • " + planLine(s))
		}
	case "subtask_start":
		fmt.Printf("▶ [%s] %s (%s)…\n", str("id"), truncate(str("goal"), 50), str("role"))
	case "subtask_done":
		fmt.Printf("✅ [%s] %s\n", str("id"), "done")
	case "subtask_retry":
		fmt.Printf("🔁 [%s] retry: %s\n", str("id"), truncate(str("error"), 40))
	case "subtask_failed":
		fmt.Printf("❌ [%s] failed: %s\n", str("id"), truncate(str("error"), 50))
	case "synthesis_done":
		fmt.Println("🧬 " + tr("msg.synthesizing_final"))
	case "intent":
		items, _ := o["items"].([]interface{})
		for _, raw := range items {
			it, _ := raw.(map[string]interface{})
			if st, _ := it["status"].(string); st != "doing" {
				continue
			}
			goal, _ := it["goal"].(string)
			if goal != "" && lastStep[str("id")] != goal {
				lastStep[str("id")] = goal
				fmt.Printf("   ↳ [%s] %s\n", str("id"), truncate(goal, 70))
			}
			break
		}
	}
}

func planLine(raw interface{}) string {
	s, _ := raw.(map[string]interface{})
	if s == nil {
		return "?"
	}
	get := func(k string) string { v, _ := s[k].(string); return v }
	extra := ""
	if m := get("model"); m != "" {
		extra += " 🧠" + m
	}
	if tgt := get("target"); tgt != "" && tgt != "local" {
		extra += " 🌐" + tgt
	}
	if deps, ok := s["deps"].([]interface{}); ok && len(deps) > 0 {
		names := make([]string, 0, len(deps))
		for _, d := range deps {
			names = append(names, fmt.Sprint(d))
		}
		extra += " ←" + strings.Join(names, ",")
	}
	return fmt.Sprintf("[%s] %s (%s)%s", get("id"), truncate(get("goal"), 50),
		get("role"), extra)
}

func answerPlanAck(cfg Config, ackID string, subtasks []map[string]interface{}) {
	decision := "run"
	if !autoApprovePlans {
		fmt.Fprintf(os.Stderr, "\n🧩 %s\n", tr("plan.ack_title"))
		for _, s := range subtasks {
			raw := map[string]interface{}{}
			for k, v := range s {
				raw[k] = v
			}
			fmt.Fprintln(os.Stderr, "   • "+planLine(raw))
		}
		fmt.Fprintf(os.Stderr, "▶ %s / ✕ %s  [y/N] ", tr("plan.ack_run"),
			tr("plan.ack_cancel"))
		if !isYes(readLineUnbuffered()) {
			decision = "cancel"
		}
	}
	body, status, err := postJSON(httpClient(15), cfg.MCPURL+"/agent/plan_ack",
		map[string]interface{}{
			"ack_id": ackID, "decision": decision,

			"allow_expand": false,
			"subtasks":     subtasks,
		}, nil)
	if err != nil || status != 200 {
		fmt.Fprintf(os.Stderr, "⚠ could not answer the plan: %v %s\n", err,
			truncate(string(body), 120))
	}
}

func answerHandsConsent(cfg Config, ackID, model, host string) {
	fmt.Fprintf(os.Stderr, "\n🖐 %s\n%s\n", tr("hands.consent_title"),
		tr("hands.consent_text", "model", model, "host", host))
	fmt.Fprintf(os.Stderr, "▶ %s / ✕ %s  [y/N] ", tr("hands.consent_yes"),
		tr("hands.consent_no"))
	decision := "cancel"
	if isYes(readLineUnbuffered()) {
		decision = "run"
	}
	body, status, err := postJSON(httpClient(15), cfg.MCPURL+"/agent/plan_ack",
		map[string]interface{}{"ack_id": ackID, "decision": decision}, nil)
	if err != nil || status != 200 {
		fmt.Fprintf(os.Stderr, "⚠ could not answer the consent question: %v %s\n",
			err, truncate(string(body), 120))
	}
}

func readLineUnbuffered() string {
	var out []byte
	buf := make([]byte, 1)
	for {
		n, err := os.Stdin.Read(buf)
		if n > 0 {
			if buf[0] == '\n' {
				break
			}
			out = append(out, buf[0])
		}
		if err != nil {
			break
		}
	}
	return string(out)
}

func isYes(line string) bool {
	s := strings.ToLower(strings.TrimSpace(line))
	return s == "y" || s == "yes" || s == "s" || s == "si" || s == "sí"
}
