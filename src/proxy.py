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
        # TODO: create a proxysenderpool with httpconnections to the webservers.

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
        # TODO: cleanup stuff
        self.serversocket.close()

class ReverseHTTPProxy(ReverseProxy):

    def __init__(self, host, port, workersize=8):
        ReverseProxy.__init__(self, host, port)
        # create a threadpool with listening threadpool workers
        self.listenpool = ProxyListenerPool(listenersamount=workersize)
    
    def start(self):
        #start listeners
        self.listenpool.start()
        #start accepting clients
        ReverseProxy.start(self)

class ProxyWorker(multiprocessing.Process):
    __metaclass__ = ABCMeta

    def __init__(self, id, task):
        self.id = id
        # Create the process
        multiprocessing.Process.__init__(self, target=task)

    @abstractmethod
    def handle_connection(self):
        pass


class ProxyListener(ProxyWorker):

    def __init__(self, id):
        self.__workerpipe, self.visitorpipe = multiprocessing.Pipe()
        ProxyWorker.__init__(self, id, self.handle_connection)

    def __del__(self):
        self.__workerpipe.close()

    def handle_connection(self):
        # TODO: change this to while running or something
        while True:
            # get a new client
            fd = reduction.recv_handle(self.__workerpipe) # stop blocking if shutdown
            clientconn =  socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
            try:
                while True:
                    data = clientconn.recv(4096)
                    if not data:
                        break
                    print "Received data:", data
                    clientconn.send("Hello from proxyworker %d" % (self.id))
            finally:
                clientconn.close()

    def getpipe(self):
        return self.visitorpipe

class ProxySender(ProxyWorker):
    
    def __init__(self):
        pass

    def handle_connection(self):
        pass

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

class ProxyListenerPool(ProxyPool):

    def __init__(self, listenersamount=8):
        ProxyPool.__init__(self)
        self.create_workers(listenersamount)

    def create_workers(self, worker_amount=8, **args):
        for i in range(0,worker_amount):
            ProxyPool.add_worker(self,ProxyListener(i)) 

    def start(self):
        for worker in self.workers:
            worker.start()


class ProxySenderPool(ProxyPool):
    
    def __init__(self):
        ProxyPool.__init__(self)

    def create_workers(self, worker_amount=8, **args):
        pass

