"""Start Article2Pod from any folder:  python serve.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from a2p import web

if __name__ == '__main__':
    web.serve()
