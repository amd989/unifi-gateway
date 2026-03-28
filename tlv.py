# -*- coding: utf-8 -*-


class TLV:

    def __init__(self):
        self.results = bytearray()

    def add(self, type, value):
        data = bytearray([type, ((len(value) >> 8) & 0xFF), (len(value) & 0xFF)])
        data.extend(value)
        self.results.extend(data)

    def get(self, **kwargs):
        return self.results


class UnifiTLV(TLV):

    def get(self, version, command):
        length = len(self.results)
        value = bytearray([version, command, (length >> 8) & 0xFF, length & 0xFF])
        value.extend(self.results)
        return value
