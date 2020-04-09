package main

import (
	"context"
	"log"
	"net/http"
	"net/http/httputil"
	"os"
	"time"
)

type Handler struct {
	server *http.Server
}

func main() {
	handler := &Handler{}
	server := &http.Server{Addr: ":2197", Handler: handler}
	handler.server = server
	log.Printf("Serving on https://0.0.0.0:2197")
	log.Fatal(server.ListenAndServeTLS(".test-server-certificate.pem", ".test-server-private-key.pem"))
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
