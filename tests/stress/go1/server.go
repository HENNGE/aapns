package main

import (
	"log"
	"net/http"
	"net/http/httputil"
	"time"
)

func main() {
	srv := &http.Server{Addr: ":2197", Handler: http.HandlerFunc(handle)}
	log.Printf("Serving on https://0.0.0.0:2197")
	log.Fatal(srv.ListenAndServeTLS("cert.pem", "key.pem"))
}

func handle(w http.ResponseWriter, r *http.Request) {
	dump, err := httputil.DumpRequest(r, true)
	if err != nil {
		log.Fatal(err)
		return
	}
	log.Printf("%q", dump)
	log.Printf("req on protocol %s", r.Proto)
	time.Sleep(time.Second)
	w.Write([]byte("{}"))
}
