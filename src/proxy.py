#!/usr/bin/env python

"""
Reverse proxy implementation with a threadpool for client connections and a
threadpool for connections with servers.
Makes use of caching.
"""

from abc import ABCMeta, abstractmethod
import socket
import sys
import multiprocessing
from multiprocessing import reduction
from BaseHTTPServer import BaseHTTPRequestHandler
from StringIO import StringIO
import email

"""
# Using this new class is really easy!

request = HTTPRequest(request_text)

print request.error_code       # None  (check this first)
print request.command          # "GET"
print request.path             # "/who/ken/trust.html"
print request.request_version  # "HTTP/1.1"
print len(request.headers)     # 3
print request.headers.keys()   # ['accept-charset', 'host', 'accept']
print request.headers['host']  # "cm.bell-labs.com"
"""
class HTTPRequest(BaseHTTPRequestHandler):
    def __init__(self, request_text):
        self.rfile = StringIO(request_text)
        self.raw_requestline = self.rfile.readline()
        self.error_code = self.error_message = None
        self.parse_request()

    def send_error(self, code, message):
        self.error_code = code
        self.error_message = message

def eprint(*args, **kwargs):
    print >>sys.stderr, args

def fatal(msg):
    eprint(msg)
    sys.exit(1)

class ReverseProxy(object):

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.server_addr = (host, port)
        self.serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.serversocket.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)

    def accept_clients(self):
        try:
            self.serversocket.bind(self.server_addr)
        except socket.error as msg:
            eprint('Bind failed. Error Code :', str(msg[0]), 'Message', msg[1])
            fatal(msg)
        self.serversocket.listen(1)
        while True:
            # proxyserver blocking at accept
            clientconn, client_addr = self.serversocket.accept()
            worker = self.listenpool.get_worker()
            #send socket to worker
            reduction.send_handle(worker.getpipe(),clientconn.fileno(), worker.pid)
            self.listenpool.free_worker(worker)
            clientconn.close()

    def start(self):
        self.accept_clients()

    def stop(self):
        self.serversocket.close()

class ReverseHTTPProxy(ReverseProxy):

    def __init__(self, host, port, listenpoolsize=8, senderpoolsize=8, **args):
        ReverseProxy.__init__(self, host, port)
        # create a threadpool with listening threadpool workers
        serverhost = "127.0.0.1"
        serverport = 8000
        self.senderpool = ProxySenderPool(serverhost, serverport, sendersamount=8) 
        self.listenpool = ProxyListenerPool(self.senderpool, listenersamount=senderpoolsize)
    
    def start(self):
        self.senderpool.start()
        #start listeners
        self.listenpool.start()
        #start accepting clients
        ReverseProxy.start(self)

class ProxyWorker(multiprocessing.Process):
    __metaclass__ = ABCMeta

    def __init__(self, id, task):
        self.id = id
        self._workerpipe, self.visitorpipe = multiprocessing.Pipe()
        # Create the process
        multiprocessing.Process.__init__(self, target=task)

    @abstractmethod
    def handle_connection(self):
        pass

    def getpipe(self):
        return self.visitorpipe

class ProxyListener(ProxyWorker):

    def __init__(self, id, senderpool):
        ProxyWorker.__init__(self, id, self.handle_connection)
        self.senderpool = senderpool

    def __del__(self):
        self._workerpipe.close()

    def receive_http_request(self, sock, bufsize=4096):
            data = ""
            while "\r\n\r\n" not in data:
                newdata = sock.recv(bufsize)
                if not newdata:
                    break
                data += newdata 
            headerlength = 0
            try:
                    headerlength = data.index("\r\n\r\n") + 4
            except ValueError:
                    # no end of header found, header to large?
                    return None
            raw_header = data[:headerlength]
            requestheader = HTTPRequest(raw_header)
            if 'content-length' in requestheader.headers.keys():
                body_length = requestheader.headers['Content-Length']
            elif len(data) > headerlength:
                # if request contains a message body and no content-length, 
                # return 400 bad request or 411 length
                response = ("<!DOCTYPE HTML>"
                            "<html><head></head><body>"
                            "<h1>Your request contains a message body, but no content-length</h1>"
                            "</body></html>")
                requestheader.send_response(400,response)
                return requestheader
            else:
                # There is no body in this request, return request
                return requestheader
            while (len(data) - headerlength) < body_length:
                    data += sock.recv(bufsize)
            # Return the request with the complete body
            return HTTPRequest(data)

    def handle_connection(self):
        RECV_SIZE = 4096
        while True:
            # get a new client
            fd = reduction.recv_handle(self._workerpipe) # stop blocking if shutdown
            clientconn =  socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
            try:
                    clientrequest = self.receive_http_request(clientconn, bufsize=RECV_SIZE)
                    if clientrequest is None:
                        continue
                    elif clientrequest.error_code == 400:
                        # request contains a message body, but no content-length
                        clientconn.sendall(clientrequest.rfile.getvalue())
                        continue
                    # if in cache, send cache
                    
                    # else, send request to ProxySenderPool
                    sender_worker = self.senderpool.get_worker()
                    visitorpipe = sender_worker.getpipe()
                    visitorpipe.send(clientrequest)
                    serverresponse = visitorpipe.recv()
                    self.senderpool.free_worker(sender_worker)
                    clientconn.sendall(serverresponse)
            finally:
                clientconn.shutdown(1)
                clientconn.close()

class ProxySender(ProxyWorker):
    
    def __init__(self, id, serverhost, serverport):
        ProxyWorker.__init__(self, id, self.handle_connection)
        self.serverhost = serverhost
        self.serverport = serverport
        self.serversock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def receive_http_response(self, sock, request, bufsize=4096):
            # send the request
            request_raw = request.rfile.getvalue()
            sock.sendall(request_raw)
            # receive the response header and some extra data
            data = ""
            newdata = ""
            while "\r\n\r\n" not in newdata:
                newdata = sock.recv(bufsize)
                if not newdata:
                    break
                data += newdata 

            headerlength = 0
            try:
                    headerlength = data.index("\r\n\r\n") + 4
            except ValueError:
                    # no end of header found, header to large?
                    return None
            raw_header = data[:headerlength]
            response_line, headers_alone = raw_header.split('\r\n', 1)
            message = email.message_from_file(StringIO(headers_alone))
            responseheader = dict(message.items())
            error_code_pos = response_line.index(' ') + 1
            error_code = int(response_line[error_code_pos:response_line.index(' ', error_code_pos)])
            # no body in a response on the HEAD command
            # no body in 1xx, 204 and 304 response
            body_length = 0
            if (request.command == "HEAD" or 100 <= error_code < 200 
                or error_code in [204,304]):
                return raw_header
            #elif 'Content-Length' in responseheader.keys():
            #    # if request contains a message body and no content-length, 
            #    # return 400 bad request or 411 length
            #    responsebody = ("<!DOCTYPE HTML>"
            #                "<html><head></head><body>"
            #                "<h1>Webserver doesn't give the content-length, but it has a body</h1>"
            #                "</body></html>")
            #    responseheader = ("HTTP/1.0 400 Bad Request\r\n"
            #                "Content-Length: %d\r\n" % (len(responsebody)) +
            #                "\r\n")
            #    return responseheader + responsebody
            #else:
            #    # There is no body in this request, return request
            #    eprint("no body, returning request")
            #    return raw_header
            
            # read till the end of the response or till the server closes the connection
            if 'Content-Length' in responseheader.keys():
                body_length = int(responseheader['Content-Length'])
                while (len(data) - headerlength) < body_length:
                    newdata = sock.recv(bufsize)
                    if not newdata:
                        break
                    data += newdata
            else:
                while True:
                    newdata = sock.recv(bufsize)
                    if not newdata:
                        break
                    data += newdata
            # Return the request with the complete body
            return data

    def handle_connection(self):
        BUFSIZE = 4096
        # create connection with a webserver and keep it alive
        while True:
            # get a new request from the proxy
            request = self._workerpipe.recv() 
            self.serversock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.serversock.connect((self.serverhost, self.serverport))
            try:
                response = self.receive_http_response(self.serversock, request, bufsize=BUFSIZE)
            finally:
                self.serversock.close()
            #response = ("HTTP/1.0 200 OK\r\n"
            #            "Server: BaseHTTP/0.3 Python/2.7.13\r\n"
            #            "Date: Sat, 04 Feb 2017 11:02:23 GMT\r\n"
            #            "\r\n\r\n"
            #            "Thread-51\r\n\r\n")

            ## send the response back to the proxy listener
            self._workerpipe.send(response)

class ProxyPool(object):
    __metaclass__ = ABCMeta

    def __init__(self):
        self.workers = []
        self.waitingworkers = multiprocessing.Queue()

    @abstractmethod
    def create_workers(self, worker_amount=8, **args):
        pass

    def add_worker(self, worker):
        self.workers.append(worker)
        self.waitingworkers.put(worker.id)

    def get_worker(self):
        return self.workers[self.waitingworkers.get()]
        
    def free_worker(self, worker):
        self.waitingworkers.put(worker.id)

    def start(self):
        for worker in self.workers:
            worker.start()

class ProxyListenerPool(ProxyPool):

    def __init__(self, senderpool, listenersamount=8):
        ProxyPool.__init__(self)
        self.senderpool = senderpool
        self.create_workers(listenersamount)

    def create_workers(self, worker_amount=8, **args):
        for i in range(0,worker_amount):
            self.add_worker(ProxyListener(i, self.senderpool)) 

class ProxySenderPool(ProxyPool):
    
    def __init__(self, serverhost, serverport, sendersamount=8):
        ProxyPool.__init__(self)
        self.serverhost = serverhost
        self.serverport = serverport
        self.create_workers(self.serverhost, self.serverport)

    def create_workers(self, serverhost, serverport, worker_amount=8, **args):
        for i in range(0, worker_amount):
            self.add_worker(ProxySender(i, serverhost, serverport)) 
