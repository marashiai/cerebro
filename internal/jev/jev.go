// Package jev is a minimal client for Jev's typed classification API.
package jev

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"time"
)

const DefaultEndpoint = "https://api.typesafe.ai/v1/systemone"

// Question is one typed choice: Jev picks a criterion and reports probabilities.
type Question struct {
	Type         string            `json:"type"`
	Instructions string            `json:"instructions,omitempty"`
	Criteria     map[string]string `json:"criteria"`
}

// Answer is Jev's validated choice for one question.
type Answer struct {
	Choice        string             `json:"choice"`
	Confidence    float64            `json:"confidence"`
	Probabilities map[string]float64 `json:"probabilities"`
}

// Response holds validated answers keyed by question name.
type Response struct {
	Model   string            `json:"model"`
	Answers map[string]Answer `json:"answers"`
	Usage   json.RawMessage   `json:"usage,omitempty"`
}

type Client struct {
	Key, Model, Endpoint string
	HTTP                 *http.Client
}

func New(key, model, endpoint string) (*Client, error) {
	if key == "" || model == "" {
		return nil, errors.New("jev requires an API key and model")
	}
	if endpoint == "" {
		endpoint = DefaultEndpoint
	}
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" ||
		!(parsed.Scheme == "https" || parsed.Scheme == "http" && (parsed.Hostname() == "localhost" || parsed.Hostname() == "127.0.0.1")) {
		return nil, fmt.Errorf("jev endpoint must be HTTPS (or loopback HTTP) without credentials or query: %s", endpoint)
	}
	return &Client{Key: key, Model: model, Endpoint: endpoint, HTTP: &http.Client{Timeout: 20 * time.Second}}, nil
}

// Evaluate sends state and questions and returns validated answers. The raw
// body is returned for tracing even when validation fails.
func (c *Client) Evaluate(ctx context.Context, state any, questions map[string]Question) (*Response, []byte, error) {
	body, err := json.Marshal(map[string]any{"model": c.Model, "state": state, "questions": questions})
	if err != nil {
		return nil, nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.Endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, nil, err
	}
	request.Header.Set("Authorization", "Bearer "+c.Key)
	request.Header.Set("Content-Type", "application/json")
	reply, err := c.HTTP.Do(request)
	if err != nil {
		return nil, nil, err
	}
	defer reply.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(reply.Body, 256<<10))
	if err != nil {
		return nil, raw, err
	}
	if reply.StatusCode/100 != 2 {
		return nil, raw, fmt.Errorf("jev HTTP %d", reply.StatusCode)
	}
	var response Response
	if err := json.Unmarshal(raw, &response); err != nil {
		return nil, raw, fmt.Errorf("jev response is not JSON: %w", err)
	}
	return &response, raw, validate(&response, questions)
}

// validate rejects answers that are internally inconsistent, such as a choice
// that is not the most probable option (observed once from the provider).
func validate(response *Response, questions map[string]Question) error {
	if response.Model == "" || len(response.Answers) != len(questions) {
		return errors.New("jev returned an invalid answer set")
	}
	for name, question := range questions {
		answer, ok := response.Answers[name]
		if !ok {
			return fmt.Errorf("jev omitted %s", name)
		}
		if _, ok := question.Criteria[answer.Choice]; !ok || answer.Confidence < 0 || answer.Confidence > 1 ||
			len(answer.Probabilities) != len(question.Criteria) {
			return fmt.Errorf("jev returned an invalid typed classification (%s)", name)
		}
		total, best := 0.0, 0.0
		for criterion, probability := range answer.Probabilities {
			if _, ok := question.Criteria[criterion]; !ok || probability < 0 || probability > 1 {
				return fmt.Errorf("jev returned an invalid typed classification (%s)", name)
			}
			total += probability
			best = math.Max(best, probability)
		}
		// Probabilities are rounded to two decimals by the provider.
		if math.Abs(total-1) > 0.005*float64(len(answer.Probabilities))+1e-9 || answer.Probabilities[answer.Choice] < best {
			return fmt.Errorf("jev returned an invalid typed classification (%s)", name)
		}
	}
	return nil
}
