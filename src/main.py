#!/usr/bin/env python
import proxy
import atexit
import ConfigParser
import sys

def read_config(filepath):
    config = ConfigParser.SafeConfigParser()
    config.read(filepath)
    proxy_addr = config.get("ReverseProxy", "address")
    proxy_port = config.getint("ReverseProxy", "port")
    listenprocesses = config.getint("ReverseProxy", "listenprocesses")
    # Seconds after a cache object is out of date
    cachetime = config.getint("Cache", "time")
    proxyconfig = {
        'proxy_addr' : proxy_addr,
        'proxy_port' : proxy_port,
        'listen_processes' : listenprocesses,
        'cache_time' : cachetime,
        'Servers' : [] 
    }
    for sect in config.sections():
        if sect.startswith("Server"):
            server_addr = config.get(sect, "address")
            server_port = config.getint(sect, "port")
            sending_processes = config.getint(sect, "sendingprocesses")
            server = {
                'address' : server_addr,
                'port' : server_port,
                'sending_processes' : sending_processes
            }
            proxyconfig['Servers'].append(server)
    return proxyconfig

def print_config(config):
    print "Listening on {}:{}".format(config['proxy_addr'], config['proxy_port'])
    print "Listening processes: {}".format(config['listen_processes'])
    print "Cache out of date after {} seconds".format(config['cache_time'])
    print "==================="
    print "Server pool:"
    for server in config['Servers']:
        print "-------------------"
        print "Address: {}".format(server['address'])
        print "Port: {}".format(server['port'])
        print "Sending processes: {}".format(server['sending_processes'])
        print "-------------------"
    print "==================="

if __name__ == "__main__":
    if len(sys.argv) > 1:
        configfile = sys.argv[1]
    else:
        print >>sys.stderr, "Usage: {} <configfile>".format(sys.argv[0])
        sys.exit(1)
    config = read_config(configfile)
    print_config(config)
    reverse_proxy = proxy.ReverseHTTPProxy(config['proxy_addr'], config['proxy_port'], config)
    ##atexit.register(reverse_proxy.stop())
    reverse_proxy.start()
