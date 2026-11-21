#!/usr/bin/env python3
#
# A TFTP server that only reads, for the netboot of the LiteX BIOS (README section 15).
#
#   python3 tftpd.py DIRECTORY [--bind ADDRESS] [--port 6069]
#
# It serves the files of DIRECTORY, and no file below it, to one client at a time. It answers read
# requests (RFC 1350) with the blksize option (RFC 2348); the BIOS asks for 1024 bytes. It refuses
# write requests. Port 69 needs root; the target's --tftp-port moves the BIOS to another port.
# Stop it with Ctrl-C.

import argparse
import os
import socket
import sys

sys.dont_write_bytecode = True

RRQ, WRQ, DATA, ACK, ERROR, OACK = 1, 2, 3, 4, 5, 6
TIMEOUT = 1.0
RETRIES = 5


def error_packet(code, text):
    return ERROR.to_bytes(2, "big") + code.to_bytes(2, "big") + text.encode() + b"\0"


def parse_request(packet):
    fields = packet[2:].split(b"\0")
    name, mode = fields[0].decode(errors="replace"), fields[1].decode(errors="replace").lower()
    options = {}
    for i in range(2, len(fields) - 1, 2):
        if fields[i]:
            options[fields[i].decode().lower()] = fields[i + 1].decode()
    return name, mode, options


def send_and_wait(sock, client, packet, block):
    """Sends a packet until the client acknowledges block. False if it never does."""
    for _ in range(RETRIES):
        sock.sendto(packet, client)
        while True:
            try:
                reply, sender = sock.recvfrom(65536)
            except socket.timeout:
                break
            if sender != client or len(reply) < 4:
                continue
            opcode = int.from_bytes(reply[:2], "big")
            if opcode == ERROR:
                return False
            if opcode == ACK and int.from_bytes(reply[2:4], "big") == block:
                return True
    return False


def serve_file(path, client, options, bind):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind, 0))
    sock.settimeout(TIMEOUT)
    block_size = 512
    if "blksize" in options:
        block_size = max(8, min(int(options["blksize"]), 1428))
        if not send_and_wait(sock, client, OACK.to_bytes(2, "big") + f"blksize\0{block_size}\0".encode(), 0):
            return "no ACK of the OACK"
    with open(path, "rb") as f:
        block = 1
        while True:
            data = f.read(block_size)
            packet = DATA.to_bytes(2, "big") + (block & 0xffff).to_bytes(2, "big") + data
            if not send_and_wait(sock, client, packet, block & 0xffff):
                return f"no ACK of block {block}"
            if len(data) < block_size:
                return None
            block += 1


def main():
    parser = argparse.ArgumentParser(description="Read-only TFTP server for the LiteX BIOS.")
    parser.add_argument("directory")
    parser.add_argument("--bind", default="0.0.0.0", help="Address to listen on.")
    parser.add_argument("--port", default=6069, type=int, help="UDP port (69 needs root).")
    args = parser.parse_args()
    root = os.path.realpath(args.directory)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    print(f"serving {root} on {args.bind}:{args.port}", flush=True)
    while True:
        packet, client = sock.recvfrom(65536)
        opcode = int.from_bytes(packet[:2], "big")
        if opcode == WRQ:
            sock.sendto(error_packet(2, "read only"), client)
            continue
        if opcode != RRQ:
            continue
        name, mode, options = parse_request(packet)
        path = os.path.realpath(os.path.join(root, name.lstrip("/")))
        if os.path.dirname(path) != root or not os.path.isfile(path):
            print(f"{client[0]}: {name}: not found", flush=True)
            sock.sendto(error_packet(1, "file not found"), client)
            continue
        print(f"{client[0]}: {name} ({os.path.getsize(path)} bytes, {mode}, {options})", flush=True)
        failure = serve_file(path, client, options, args.bind)
        print(f"{client[0]}: {name}: {failure or 'sent'}", flush=True)


if __name__ == "__main__":
    main()
