package main

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

func mcpDistill(cfg Config, history []Message) {
	if len(history) == 0 {
		return
	}
	body, status, err := postJSON(httpClient(cfg.Timeout+120),
		cfg.MCPURL+"/memory/distill",
		map[string]interface{}{"history": history,
			"kinds": []string{"facts", "lessons"}}, nil)
	if err != nil || status != 200 {
		return
	}
	var out struct {
		Facts   int `json:"facts"`
		Lessons int `json:"lessons"`
	}
	if json.Unmarshal(body, &out) != nil || out.Facts+out.Lessons == 0 {
		return
	}
	fmt.Printf("🧠 %d fact(s), %d lesson(s) distilled\n", out.Facts, out.Lessons)
}

func mcpCompact(cfg Config, history []Message) []Message {
	fmt.Println("📦 Compacting context (near the limit)…")
	payload := map[string]interface{}{"messages": history,
		"keep_recent": cfg.CompactKeep,

		"conv": []string{convID}}
	if cfg.modelFixed {
		payload["endpoint"] = map[string]string{
			"base_url": cfg.BaseURL, "api_key": cfg.APIKey, "model": cfg.Model,
		}
	}
	body, status, err := postJSON(httpClient(cfg.Timeout+180),
		cfg.MCPURL+"/memory/compact", payload, nil)
	if err != nil || status != 200 {
		fmt.Println("⚠ Could not compact. Continuing as is.")
		return history
	}
	var out struct {
		Compacted bool      `json:"compacted"`
		Messages  []Message `json:"messages"`
		Replaced  int       `json:"replaced"`
		Why       string    `json:"why"`

		Total int    `json:"summarised_total"`
		Since string `json:"since"`

		Before int `json:"tokens_before"`
		After  int `json:"tokens_after"`
		Recent int `json:"recent_tokens"`
		Heavy  []struct {
			Role   string `json:"role"`
			Head   string `json:"head"`
			Tokens int    `json:"tokens"`
		} `json:"recent_heaviest"`
	}
	if json.Unmarshal(body, &out) != nil || !out.Compacted || len(out.Messages) == 0 {
		fmt.Printf("⚠ Not compacted (%s). Continuing as is.\n", out.Why)
		return history
	}

	total := out.Total
	if total == 0 {
		total = out.Replaced
	}
	since := out.Since
	if since == "" {
		since = "?"
	}
	if out.Before > 0 {
		heavy := []string{}
		for i, b := range out.Heavy {
			if i >= 3 {
				break
			}
			h := b.Head
			if len(h) > 40 {
				h = h[:40]
			}
			heavy = append(heavy, fmt.Sprintf("%s %s: %s", kTok(b.Tokens), b.Role, h))
		}
		if len(heavy) == 0 {
			heavy = []string{"—"}
		}
		defer fmt.Println(tr("msg.compacted_size", "before", kTok(out.Before),
			"after", kTok(out.After), "recent", kTok(out.Recent),
			"heavy", strings.Join(heavy, "; ")))
	}
	fmt.Println(tr("msg.compacted",
		"n", fmt.Sprintf("%d", out.Replaced),
		"total", fmt.Sprintf("%d", total),
		"since", since))
	return out.Messages
}

func memoryShow(cfg Config) {
	r, err := httpClient(10).Get(cfg.MCPURL + "/memory/proposals")
	if err != nil {
		fmt.Println("❌", err)
		return
	}
	defer r.Body.Close()
	var props struct {
		Proposals []struct {
			ID         string `json:"id"`
			Action     string `json:"action"`
			TargetText string `json:"target_text"`
			NewText    string `json:"new_text"`
			Why        string `json:"why"`
			Quotes     []struct {
				TS   string `json:"ts"`
				Text string `json:"text"`
			} `json:"quotes"`
		} `json:"proposals"`
	}
	json.NewDecoder(r.Body).Decode(&props)
	if len(props.Proposals) == 0 {
		fmt.Println("Nothing awaiting your decision.")
	}
	for _, p := range props.Proposals {
		verb := map[string]string{"retire": "RETIRE", "replace": "REPLACE",
			"merge": "MERGE"}[p.Action]
		fmt.Printf("[%s] %s  «%s»\n", p.ID, verb, p.TargetText)
		if p.NewText != "" {
			fmt.Printf("      → «%s»\n", p.NewText)
		}
		if p.Why != "" {
			fmt.Printf("      why: %s\n", p.Why)
		}

		for _, q := range p.Quotes {
			fmt.Printf("      ev: «%s»  (%s)\n", truncate(q.Text, 100),
				truncate(q.TS, 10))
		}
		fmt.Printf("      → /memory ok %s   |   /memory no %s\n", p.ID, p.ID)
	}

	r2, err := httpClient(10).Get(cfg.MCPURL + "/memory/autolog?limit=8")
	if err != nil {
		return
	}
	defer r2.Body.Close()
	var alog struct {
		LastRun string `json:"last_run"`
		Entries []struct {
			ID      string                 `json:"id"`
			Kind    string                 `json:"kind"`
			Undone  bool                   `json:"undone"`
			Summary map[string]interface{} `json:"summary"`
		} `json:"entries"`
	}
	json.NewDecoder(r2.Body).Decode(&alog)
	if alog.LastRun != "" {
		fmt.Printf("Last pass: %s\n", truncate(alog.LastRun, 16))
	}
	if len(alog.Entries) > 0 {
		fmt.Println("Applied by itself (reversible):")
		for _, e := range alog.Entries {
			mark := ""
			if e.Undone {
				mark = "↩ UNDONE · "
			}
			merged, _ := e.Summary["merged"].(string)
			if merged == "" {
				merged = e.Kind
			}
			fmt.Printf("  [%s] %s%s\n", e.ID, mark, truncate(merged, 90))
			if !e.Undone {
				fmt.Printf("        → /memory undo %s\n", e.ID)
			}
		}
	}
}

func memoryRun(cfg Config) {
	fmt.Println("Running the whole pass (rule merges applied, facts only proposed)…")
	body, status, err := postJSON(httpClient(cfg.Timeout+600),
		cfg.MCPURL+"/memory/review", map[string]interface{}{"limit": 6}, nil)
	if err != nil || status != 200 {
		fmt.Println("❌ the pass did not run:", err)
		return
	}
	var out struct {
		Lessons struct {
			Merged      []interface{} `json:"merged"`
			Deactivated int           `json:"deactivated"`
			Error       string        `json:"error"`
		} `json:"lessons"`
		Facts struct {
			Asked   int           `json:"asked"`
			NoTrace []interface{} `json:"no_trace"`
			Error   string        `json:"error"`
		} `json:"facts"`
		Duplicates struct {
			LookedAt int           `json:"looked_at"`
			Proposed []interface{} `json:"proposed"`
			Error    string        `json:"error"`
		} `json:"duplicates"`
	}
	json.Unmarshal(body, &out)

	for name, e := range map[string]string{"lessons": out.Lessons.Error,
		"facts": out.Facts.Error, "duplicates": out.Duplicates.Error} {
		if e != "" {
			fmt.Printf("⚠ %s half FAILED: %s\n", name, e)
		}
	}
	fmt.Printf("rules: %d merge(s), %d retired · intentions: %d with a trace, "+
		"%d without · duplicates/noise: %d facts read, %d proposal(s)\n",
		len(out.Lessons.Merged), out.Lessons.Deactivated, out.Facts.Asked,
		len(out.Facts.NoTrace), out.Duplicates.LookedAt,
		len(out.Duplicates.Proposed))
	memoryShow(cfg)
}

func memoryDecide(cfg Config, id string, accept bool) {
	body, status, err := postJSON(httpClient(15),
		cfg.MCPURL+"/memory/proposals/"+id,
		map[string]interface{}{"accept": accept}, nil)
	if err != nil || status != 200 {
		fmt.Println("❌", err)
		return
	}
	var out map[string]interface{}
	json.Unmarshal(body, &out)
	if e, _ := out["error"].(string); e != "" {
		fmt.Println("❌", e)
		return
	}
	if accept {
		fmt.Println("✅ applied.")
	} else {
		fmt.Println("Rejected — it will not be proposed again.")
	}
}

func memoryUndo(cfg Config, id string) {
	body, status, err := postJSON(httpClient(15),
		cfg.MCPURL+"/memory/autolog/"+id+"/undo",
		map[string]interface{}{}, nil)
	if err != nil || status != 200 {
		fmt.Println("❌", err)
		return
	}
	var out map[string]interface{}
	json.Unmarshal(body, &out)
	if e, _ := out["error"].(string); e != "" {
		fmt.Println("❌", e)
		return
	}
	fmt.Println("↩ undone: the merged rule is gone, the ones it replaced are active again.")
}

func kTok(n int) string {
	if n >= 1000 {
		return fmt.Sprintf("%.1fk", float64(n)/1000)
	}
	return fmt.Sprintf("%d", n)
}

func showContextMap(cfg Config, history []Message) {
	body, status, err := postJSON(httpClient(60), cfg.MCPURL+"/agent/context_map",
		map[string]interface{}{"messages": history, "code_project": sessionProject}, nil)
	if err != nil || status != 200 {
		fmt.Println(tr("ctx.map_fail", "e", fmt.Sprintf("HTTP %d %v", status, err)))
		return
	}
	var out struct {
		Error   string `json:"error"`
		Total   int    `json:"total_tokens"`
		History struct {
			Messages int            `json:"messages"`
			Tokens   int            `json:"tokens"`
			ByRole   map[string]int `json:"by_role"`
		} `json:"history"`
		Tools struct {
			Carried int `json:"carried"`
			Tokens  int `json:"tokens"`
		} `json:"tools"`
		Blocks []struct {
			Role   string `json:"role"`
			Head   string `json:"head"`
			Tokens int    `json:"tokens"`
		} `json:"by_block"`
	}
	if json.Unmarshal(body, &out) != nil || out.Error != "" {
		fmt.Println(tr("ctx.map_fail", "e", out.Error))
		return
	}
	measured := "—"
	if lastPromptTokens > 0 {
		measured = kTok(lastPromptTokens)
	}
	fmt.Println(tr("ctx.map_head", "total", kTok(out.Total), "measured", measured,
		"n", fmt.Sprintf("%d", out.History.Messages), "tools", kTok(out.Tools.Tokens),
		"carried", fmt.Sprintf("%d", out.Tools.Carried)))
	if len(out.History.ByRole) > 0 {
		parts := []string{}
		for r, n := range out.History.ByRole {
			parts = append(parts, fmt.Sprintf("%s: %s", r, kTok(n)))
		}
		sort.Strings(parts)
		fmt.Println(tr("ctx.map_roles", "roles", strings.Join(parts, " · ")))
	}
	fmt.Println(tr("ctx.map_blocks"))
	for i, b := range out.Blocks {
		if i >= 15 {
			break
		}
		head := strings.TrimSpace(b.Head)
		if len(head) > 70 {
			head = head[:70]
		}
		fmt.Printf("  %7s  %-9.9s %s\n", kTok(b.Tokens), b.Role, head)
	}
	fmt.Println(tr("ctx.map_hint"))
}
