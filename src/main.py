#!/usr/bin/env python
import proxy
import atexit

if __name__ == "__main__":
    # TODO: read config
    reverse_proxy = proxy.ReverseHTTPProxy("localhost",8080)
    #atexit.register(reverse_proxy.stop())
    reverse_proxy.start()
