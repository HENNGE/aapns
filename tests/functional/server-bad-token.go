package main

import (
	"log"
	"net/http"
	"net/http/httputil"
	"os"
	"time"
)

func main() {
	srv := &http.Server{Addr: "localhost:2197", Handler: http.HandlerFunc(handle)}
	log.Printf("Serving on https://localhost:2197")
	log.Fatal(srv.ListenAndServeTLS("tests/functional/test-server-certificate.pem", "tests/functional/test-server-private-key.pem"))
}

func handle(w http.ResponseWriter, r *http.Request) {
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
