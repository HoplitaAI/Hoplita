package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

var i18nStrings map[string]string

func loadI18n(langCode string) {
	i18nStrings = map[string]string{}
	if langCode == "" {
		langCode = "en"
	}
	file := langCode + ".json"
	candidates := []string{}

	if exe, err := os.Executable(); err == nil {
		candidates = append(candidates, filepath.Join(filepath.Dir(exe), "lang", file))
	}

	candidates = append(candidates, filepath.Join("lang", file))

	if runtime.GOOS == "windows" {

		if appdata := os.Getenv("APPDATA"); appdata != "" {
			candidates = append(candidates, filepath.Join(appdata, "hoplita", "lang", file))
		}
	} else {

		for _, d := range installDirs() {
			candidates = append(candidates, filepath.Join(d, "lang", file))
		}
	}
	for _, path := range candidates {
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var raw map[string]interface{}
		if json.Unmarshal(data, &raw) != nil {
			continue
		}
		for k, v := range raw {
			if s, ok := v.(string); ok {
				i18nStrings[k] = s
			}
		}
		return
	}
}

func tr(key string, args ...string) string {
	s, ok := i18nStrings[key]
	if !ok {
		return key
	}

	for i := 0; i+1 < len(args); i += 2 {
		s = strings.ReplaceAll(s, "{"+args[i]+"}", args[i+1])
	}
	return s
}
