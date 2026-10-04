// Command jev-replay classifies recorded moments with the same context and
// decision code as `cerebro run`, for calibrating Jev against known answers.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"sync"

	"github.com/marashiai/cerebro/internal/agent"
	"github.com/marashiai/cerebro/internal/jev"
	"github.com/marashiai/cerebro/internal/watch"
)

type replayCase struct {
	ID         string         `json:"id"`
	Request    string         `json:"request"`
	Later      []string       `json:"later_messages"`
	History    []agent.Event  `json:"history"`
	Events     []agent.Event  `json:"events"`
	Rules      []string       `json:"rules"`
	Repository map[string]any `json:"repository"`
}

func main() {
	threshold := flag.Float64("threshold", 0.8, "minimum Jev confidence for a nudge")
	rulesFile := flag.String("rules", "", "personal standing rules added to every case")
	workers := flag.Int("workers", 4, "parallel Jev requests")
	flag.Parse()
	client, err := jev.New(os.Getenv("JEV_API_KEY"), envOr("JEV_MODEL", "jev-latest"), os.Getenv("JEV_ENDPOINT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "jev-replay:", err)
		os.Exit(1)
	}
	var personal []string
	if *rulesFile != "" {
		data, err := os.ReadFile(*rulesFile)
		if err != nil {
			fmt.Fprintln(os.Stderr, "jev-replay:", err)
			os.Exit(1)
		}
		personal = []string{"Personal rules of the user:\n" + string(data)}
	}
	cases := make(chan replayCase)
	var out sync.Mutex
	encoder := json.NewEncoder(os.Stdout)
	var group sync.WaitGroup
	for range *workers {
		group.Add(1)
		go func() {
			defer group.Done()
			for c := range cases {
				w, err := watch.New(watch.Config{Client: client, Threshold: *threshold, MaxNudges: 3, Request: c.Request,
					Instructions: append(append([]string(nil), c.Rules...), personal...), Repository: c.Repository,
					Log: func(map[string]any) {}})
				result := map[string]any{"id": c.ID}
				if err == nil {
					var record map[string]any
					var nudge *watch.Nudge
					record, nudge, err = w.Replay(context.Background(), c.Later, c.History, c.Events)
					result["record"], result["nudge"] = record, nudge
				}
				if err != nil {
					result["error"] = err.Error()
				}
				out.Lock()
				encoder.Encode(result)
				out.Unlock()
			}
		}()
	}
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 1<<20), 64<<20)
	for scanner.Scan() {
		var c replayCase
		if err := json.Unmarshal(scanner.Bytes(), &c); err != nil {
			fmt.Fprintln(os.Stderr, "jev-replay: bad case:", err)
			os.Exit(1)
		}
		cases <- c
	}
	close(cases)
	group.Wait()
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
