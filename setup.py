#!/usr/bin/env python
from setuptools import setup, find_packages
#import os

#with open("requirements.txt") as reqfile:
#    required = reqfile.read().splitlines()

setup(
        name="reverse-proxy",
        description="A reverse proxy that is used for load balancing http traffic between web servers",
        url="https://github.com/MelvinKool/reverse-proxy",
        packages=find_packages(),
        #install_requires=required
)

