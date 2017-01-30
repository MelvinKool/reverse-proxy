#!/usr/bin/env python
import proxy
import atexit
import time #TODO: remove
if __name__ == "__main__":
    # read config
    print "created"
    reverse_proxy = proxy.ReverseHTTPProxy("localhost",8080)
    #print "created 2"
    #atexit.register(reverse_proxy.stop())
    reverse_proxy.start()
