package main

import (
	"bytes"
	"context"
	"encoding/json"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"os"
	"time"
)

func main() {
	listener, err := net.Listen("tcp", "localhost:0")
	if err != nil {
		panic(err)
	}
	own_port := listener.Addr().(*net.TCPAddr).Port
	data := map[string]int{"port": own_port}
	jsonValue, err := json.Marshal(data)
	if err != nil {
		panic(err)
	}
	req, err := http.NewRequest("POST", "http://localhost:"+os.Args[1], bytes.NewBuffer(jsonValue))
	if err != nil {
		panic(err)
	}
	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		panic(err)
	}
	if resp.StatusCode != 204 {
		log.Fatalf("Response status not 204: %d", resp.StatusCode)
	}
	log.Printf("Serving on https://localhost:%d", own_port)
	srv := makeServer(os.Args[2])
	log.Fatal(srv.ServeTLS(listener, "tests/functional/test-server-certificate.pem", "tests/functional/test-server-private-key.pem"))
}

func makeServer(flavour string) *http.Server {
	switch flavour {
	case "ok":
		return &http.Server{Handler: http.HandlerFunc(handle_ok)}
	case "bad-token":
		return &http.Server{Handler: http.HandlerFunc(handle_bad_token)}
	case "terminates-connection":
		handler := &Handler{}
		server := &http.Server{Handler: handler}
		handler.server = server
		return server
	default:
		log.Fatalf("Unexpected flavour: %s", os.Args[2])
		return nil
	}
}

func handle_ok(w http.ResponseWriter, r *http.Request) {
	dump, err := httputil.DumpRequest(r, true)
	if err != nil {
		log.Fatal(err)
		return
	}
	w.Header().Set("apns-id", "42424242-4242-4242-4242-424242424242")
	os.Stdout.Write(append(dump, "\n\n"...))
	time.Sleep(time.Second / 4)
	w.Write([]byte("{}"))
}

type Handler struct {
	server *http.Server
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	dump, err := httputil.DumpRequest(r, true)
	if err != nil {
		log.Fatal(err)
		return
	}
	os.Stdout.Write(append(dump, "\n\n"...))
	time.Sleep(time.Second)
	w.Write([]byte("{}"))
	h.server.Shutdown(context.Background())
}

func handle_bad_token(w http.ResponseWriter, r *http.Request) {
	dump, err := httputil.DumpRequest(r, true)
	if err != nil {
		log.Fatal(err)
		return
	}
	w.Header().Set("apns-id", "42424242-4242-4242-4242-424242424242")
	w.WriteHeader(400)
	os.Stdout.Write(append(dump, "\n\n"...))
	time.Sleep(time.Second)
	w.Write([]byte("{\"reason\": \"BadDeviceToken\"}"))
}
