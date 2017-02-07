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

def eprint(*args, **kwargs):
    print >>sys.stderr, args

def fatal(msg):
    eprint(msg)
    sys.exit(1)

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

class HTTPResponse(object):

    def __init__(self, responsecontent):
        self.header = None
        self.raw_header = None
        self.body = None
        self.error_code = None
        # This throws a ValueError if no header is found, caller should handle this!
        self.parse_response(responsecontent)
    
    def set_header(self, header):
        response_line, headers_alone = header.split('\r\n', 1)
        error_code_pos = response_line.index(' ') + 1
        self.error_code = int(response_line[error_code_pos:response_line.index(' ', error_code_pos)])
        # parse header
        message = email.message_from_file(StringIO(headers_alone))
        self.header = dict(message.items())

    def set_body(self, body):
        self.body = body

    def parse_response(self, data):
        # try to extract the header
        self.headerlength = 0
        try:
                self.headerlength = data.index("\r\n\r\n") + 4
        except ValueError:
                # no end of header found, header to large?
                raise
        self.raw_header = data[:self.headerlength]
        self.set_header(self.raw_header)
        if(self.headerlength < len(data)):
            #set the body to the part after the header
            self.set_body(data[self.headerlength:])

    """
        Converts response back to a string
    """
    def __str__(self):
        return self.raw_header + self.body

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

    def __init__(self, host, port, config):
        ReverseProxy.__init__(self, host, port)
        cachingsystem = CachingSystem()
        cachingsystem.set_caching_time(config['cache_time'])
        self.senderpool = ProxySenderPool(config['Servers'], cachingsystem) 
        # create a threadpool with listening threadpool workers
        self.listenpool = ProxyListenerPool(self.senderpool, cachingsystem,
                                            listenersamount=config['listen_processes'])
    
    def start(self):
        self.senderpool.start()
        #start listeners
        self.listenpool.start()
        #start accepting clients
        ReverseProxy.start(self)

class ProxyWorker(multiprocessing.Process):
    __metaclass__ = ABCMeta

    def __init__(self, id, task, cachingsystem):
        self.id = id
        self._workerpipe, self.visitorpipe = multiprocessing.Pipe()
        self.cachingsystem = cachingsystem
        # Create the process
        multiprocessing.Process.__init__(self, target=task)

    @abstractmethod
    def handle_connection(self):
        pass

    def getpipe(self):
        return self.visitorpipe

class ProxyListener(ProxyWorker):

    def __init__(self, id, senderpool, cachingsystem):
        ProxyWorker.__init__(self, id, self.handle_connection, cachingsystem)
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
            fd = reduction.recv_handle(self._workerpipe)
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
                    # else, get the response from a server
                    response = None
                    # TODO:response = self.cachingsystem.get_from_cache(clientrequest)
                    if response is None:
                        sender_worker = self.senderpool.get_worker()
                        visitorpipe = sender_worker.getpipe()
                        visitorpipe.send(clientrequest)
                        response = visitorpipe.recv()
                        self.senderpool.free_worker(sender_worker)
                    clientconn.sendall(str(response))
            finally:
                clientconn.shutdown(1)
                clientconn.close()

class ProxySender(ProxyWorker):
    
    def __init__(self, id, serverhost, serverport, cachingsystem):
        ProxyWorker.__init__(self, id, self.handle_connection, cachingsystem)
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
            http_response = HTTPResponse(data)
            # no body in a response on the HEAD command
            # no body in 1xx, 204 and 304 response
            body_length = 0
            if (request.command == "HEAD" or 100 <= http_response.error_code < 200 or 
                http_response.error_code in [204,304]):
                return http_response
            # read till the end of the response or till the server closes the connection
            if 'Content-Length' in http_response.header.keys():
                body_length = int(http_response.header['Content-Length'])
                while (len(data) - http_response.headerlength) < body_length:
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
            raw_body = data[http_response.headerlength:]
            http_response.set_body(raw_body)
            self.cachingsystem.update_cache(request, http_response)
            return http_response

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
            # send the response back to the proxy listener
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

    def __init__(self, senderpool, cachingsystem, listenersamount=8):
        ProxyPool.__init__(self)
        self.senderpool = senderpool
        self.create_workers(cachingsystem, listenersamount)

    def create_workers(self, cachingsystem, worker_amount=8, **args):
        for i in range(0,worker_amount):
            self.add_worker(ProxyListener(i, self.senderpool, cachingsystem)) 

class ProxySenderPool(ProxyPool):
    
    def __init__(self, servers, cachingsystem):
        ProxyPool.__init__(self)
        self.create_workers(servers, cachingsystem)

    def create_workers(self, servers, cachingsystem):
        i = 0
        for server in servers:
            for newproc in range(0, server['sending_processes']):
                self.add_worker(ProxySender(i, server['address'], server['port'], cachingsystem)) 
                i += 1


class CachingSystem(object):
    
    def __init__(self):
        self.cache = {}
        self.cachelock = multiprocessing.Lock()
        self.cachingtime = 3600
    
    """
        Updates the cache.
        Returns True if cache is updated, else otherwise.
    """
    def update_cache(self, request, response):
        # parse request
        
        # update cache if the response is 200 and the request command is get
        if request.command == "GET" and response.error_code == 200:
            self.cachelock.acquire()
            # update cache
            # TODO
            self.cachelock.release()
            return True
        return False

    """
        Handles the caching of a request.
        If the requested resource is not found, or the cache is out of date, None is returned.
        The object is returned, otherwise.
    """
    def get_from_cache(self, request):
        cachedobject = None
        self.cachelock.acquire()
        if request.path in self.cache:
            cachedobject = self.cache[request.path]
            # TODO: if cached item is out of date, delete it from the cache
        self.cachelock.release()
        return cachedobject

    """
        Set the amount of seconds till a cached item becomes out of date
    """
    def set_caching_time(self, time):
        self.cachingtime = time
