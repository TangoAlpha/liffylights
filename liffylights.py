'''
liffylights by TangoAlpha - LIFX Python library

https://github.com/TangoAlpha/liffylights

Published under the MIT license - See LICENSE file for more details.

Not associated with or endorsed by LiFi Labs, Inc. (http://www.lifx.com/)
'''
# pylint: disable=missing-docstring
import threading
import time
import queue
import socket
import io
import ipaddress
import struct
from struct import pack
from enum import IntEnum

UDP_PORT = 56700              # udp port for listening socket
UDP_IP = "0.0.0.0"            # address for listening socket
BUFFERSIZE = 1024             # socket buffer size
SHORT_MAX = 65535             # short int maximum
BYTE_MAX = 255                # byte maximum
SEQUENCE_BASE = 1             # packet sequence base (0 is for bulb sends)
SEQUENCE_COUNT = 255          # packet sequence count
ACK_RESEND = 0.2              # resend packets every n seconds
ACK_TIMEOUT = 5               # seconds before giving up on packet

HUE_MIN = 0
HUE_MAX = 65535
SATURATION_MIN = 0
SATURATION_MAX = 65535
BRIGHTNESS_MIN = 0
BRIGHTNESS_MAX = 65535
TEMP_MIN = 2500
TEMP_MAX = 9000


class PayloadType(IntEnum):
    """ Message payload types. """
    GETSERVICE = 2
    STATESERVICE = 3
    GETHOSTINFO = 12
    STATEHOSTINFO = 13
    GETHOSTFIRMWARE = 14
    STATEHOSTFIRMWARE = 15
    GETWIFIINFO = 16
    STATEWIFIINFO = 17
    GETWIFIFIRMWARE = 18
    STATEWIFIFIRMWARE = 19
    GETPOWER1 = 20
    SETPOWER1 = 21
    STATEPOWER1 = 22
    GETLABEL = 23
    SETLABEL = 24
    STATELABEL = 25
    GETVERSION = 32
    STATEVERSION = 33
    GETINFO = 34
    STATEINFO = 35
    ACKNOWLEDGEMENT = 45
    GETLOCATION = 48
    STATELOCATION = 50
    GETGROUP = 51
    STATEGROUP = 53
    ECHOREQUEST = 58
    ECHORESPONSE = 59
    GET = 101
    SETCOLOR = 102
    STATE = 107
    GETPOWER2 = 116
    SETPOWER2 = 117
    STATEPOWER2 = 118


class Power(IntEnum):
    """ Power settings. """
    BULB_ON = 65535
    BULB_OFF = 0


class LiffyLights():
    """ Provides liffylights base class. """
    def __init__(self, device_callback, power_callback, color_callback,
                 server_addr=None, broadcast_addr=None):
        self._device_callback = device_callback
        self._power_callback = power_callback
        self._color_callback = color_callback

        self._packet_lock = threading.Lock()
        self._send_lock = threading.Lock()

        self._packets = []

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._seq = {"lock": threading.Lock(), "sequence": -1}

        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind((UDP_IP, UDP_PORT))

        self._listener = threading.Thread(target=self.packet_listener)
        self._listener.daemon = True
        self._listener.start()

        self._manager = threading.Thread(target=self.packet_manager)
        self._manager.daemon = True
        self._manager.start()

        self._cmdqueue = queue.Queue(maxsize=255)
        self._command_sender = threading.Thread(target=self.command_sender)
        self._command_sender.daemon = True
        self._command_sender.start()

        if server_addr is None:
            # no specific server address given, use hostname
            self._server_addr = socket.gethostbyname(socket.getfqdn())
        else:
            self._server_addr = server_addr

        if broadcast_addr is None:
            # make best guess for broadcast address
            addr = ipaddress.ip_interface(self._server_addr + "/24")
            self._broadcast_addr = str(addr.network.broadcast_address)
        else:
            self._broadcast_addr = broadcast_addr

    def get_sequence(self):
        """ Return next packet sequence number. """

        # return next packet sequence number
        with self._seq["lock"]:
            self._seq["sequence"] = \
                (self._seq["sequence"] + 1) % SEQUENCE_COUNT
            return self._seq["sequence"] + SEQUENCE_BASE

    def probe(self, address=None):
        """ Probe given address for bulb. """
        if address is None:
            address = self._broadcast_addr

        if self._sock is not None:
            sequence = self.get_sequence()

            # create "get" message
            payload = self.gen_payload_get(sequence)

            with self._send_lock:
                try:
                    self._sock.sendto(payload, (address, UDP_PORT))

                # pylint: disable=broad-except
                except Exception:
                    pass

    def gen_header(self, sequence, payloadtype):
        """ Create packet header. """
        protocol = bytearray.fromhex("00 34")
        source = bytearray.fromhex("42 52 4b 52")
        target = bytearray.fromhex("00 00 00 00 00 00 00 00")
        reserved1 = bytearray.fromhex("00 00 00 00 00 00")
        sequence = pack("<B", sequence)
        ack = pack(">B", 3)
        reserved2 = bytearray.fromhex("00 00 00 00 00 00 00 00")
        packet_type = pack("<H", payloadtype)
        reserved3 = bytearray.fromhex("00 00")

        # assemble header
        header = bytearray(protocol)
        header.extend(source)
        header.extend(target)
        header.extend(reserved1)
        header.extend(ack)
        header.extend(sequence)
        header.extend(reserved2)
        header.extend(packet_type)
        header.extend(reserved3)

        return header

    def gen_packet(self, sequence, payloadtype, payload=None):
        """ Generate packet header. """
        contents = self.gen_header(sequence, payloadtype)

        # add payload
        if payload:
            contents.extend(payload)

        # get packet size
        size = pack("<H", len(contents) << 1)

        # assemble complete packet
        packet = bytearray(size)
        packet.extend(contents)

        return packet

    def gen_payload_setcolor(self, sequence, hue, sat, bri, kel, fade):
        """ Generate "setcolor" packet payload. """
        hue = min(max(hue, HUE_MIN), HUE_MAX)
        sat = min(max(sat, SATURATION_MIN), SATURATION_MAX)
        bri = min(max(bri, BRIGHTNESS_MIN), BRIGHTNESS_MAX)
        kel = min(max(kel, TEMP_MIN), TEMP_MAX)

        reserved1 = pack("<B", 0)
        hue = pack("<H", hue)
        saturation = pack("<H", sat)
        brightness = pack("<H", bri)
        kelvin = pack("<H", kel)
        duration = pack("<I", fade)

        payload = bytearray(reserved1)
        payload.extend(hue)
        payload.extend(saturation)
        payload.extend(brightness)
        payload.extend(kelvin)
        payload.extend(duration)

        return self.gen_packet(sequence, PayloadType.SETCOLOR, payload)

    def gen_payload_get(self, sequence):
        """ Generate "get" packet payload. """
        # generate payload for Get message
        return self.gen_packet(sequence, PayloadType.GET)

    def gen_payload_setpower(self, sequence, power, fade):
        """ Generate "setpower" packet payload. """
        level = pack("<H", Power.BULB_OFF if power == 0 else Power.BULB_ON)
        duration = pack("<I", fade)

        payload = bytearray(level)
        payload.extend(duration)

        return self.gen_packet(sequence, PayloadType.SETPOWER2, payload)

    def packet_ack(self, packet, sequence):
        if packet["sequence"] == sequence:
            if packet["payloadtype"] == PayloadType.SETCOLOR:
                self._color_callback(packet["target"],
                                     packet["hue"],
                                     packet["sat"],
                                     packet["bri"],
                                     packet["kel"])

            elif packet["payloadtype"] == PayloadType.SETPOWER2:
                self._power_callback(packet["target"],
                                     packet["power"])

            return True

        return False

    def process_packet(self, sequence):
        with self._packet_lock:
            self._packets[:] = [packet for packet in self._packets
                                if not self.packet_ack(packet, sequence)]

    def packet_timeout(self, packet):
        if time.time() >= packet["sent"]:
            return True

        # resend command
        self._cmdqueue.put(packet)

        return False

    def packet_manager(self):
        while True:
            with self._packet_lock:
                self._packets[:] = [packet for packet in self._packets
                                    if not self.packet_timeout(packet)]
            time.sleep(ACK_RESEND)

    # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    def packet_listener(self):
        """ Packet listener. """

        while True:
            datastream, source = self._sock.recvfrom(BUFFERSIZE)
            ipaddr, port = source

            try:
                sio = io.BytesIO(datastream)

                dummy1, sec_part = struct.unpack("<HH",
                                                 sio.read(4))

                protocol = sec_part % 4096

                if protocol == 1024:
                    source, dummy1, dummy2, dummy3, sequence, dummy4, \
                        payloadtype, dummy5 = struct.unpack("<IQ6sBBQHH",
                                                            sio.read(32))

                    if ipaddr == self._server_addr:
                        # broadcast packet
                        pass

                    elif payloadtype == PayloadType.ACKNOWLEDGEMENT:
                        self.process_packet(sequence)

                    elif payloadtype == PayloadType.STATESERVICE:
                        serv, port = struct.unpack("<BI",
                                                   sio.read(5))

                    elif payloadtype == PayloadType.STATEHOSTINFO:
                        sig, _tx, _rx, res = struct.unpack("<fIIh",
                                                           sio.read(14))

                    elif payloadtype == PayloadType.STATEHOSTFIRMWARE:
                        build, res, ver = struct.unpack("<QQI",
                                                        sio.read(20))

                    elif payloadtype == PayloadType.STATEWIFIINFO:
                        sig, _tx, _rx, res = struct.unpack("<fIIh",
                                                           sio.read(14))

                    elif payloadtype == PayloadType.STATEWIFIFIRMWARE:
                        build, _reserved, ver = struct.unpack("<QQI",
                                                              sio.read(20))

                    elif payloadtype == PayloadType.STATEPOWER1:
                        level, = struct.unpack("<H",
                                               sio.read(2))

                    elif payloadtype == PayloadType.STATELABEL:
                        label, = struct.unpack("<32s",
                                               sio.read(32))

                    elif payloadtype == PayloadType.STATEVERSION:
                        ven, prod, ver = struct.unpack("<HHH",
                                                       sio.read(6))

                    elif payloadtype == PayloadType.STATEINFO:
                        _tm, uptm, dwntm = struct.unpack("<QQQ",
                                                         sio.read(24))

                    elif payloadtype == PayloadType.STATELOCATION:
                        loc, label, upd = struct.unpack("<10s32sQ",
                                                        sio.read(50))

                    elif payloadtype == PayloadType.STATEGROUP:
                        grp, label, upd = struct.unpack("<16s32sQ",
                                                        sio.read(56))

                    elif payloadtype == PayloadType.ECHORESPONSE:
                        dummy1, = struct.unpack("<64s",
                                                sio.read(64))

                    elif payloadtype == PayloadType.STATE:
                        hue, sat, bri, kel, dummy1, power, label, dummy2 = \
                            struct.unpack("<HHHHhH32sQ",
                                          sio.read(52))

                        name = label.decode('ascii')
                        name = name.replace('\x00', '')

                        self._device_callback(ipaddr, name, power, hue,
                                              sat, bri, kel)

                    elif payloadtype == PayloadType.STATEPOWER2:
                        level, = struct.unpack("<H",
                                               sio.read(2))

            # pylint: disable=broad-except
            except Exception:
                pass

    def command_sender(self):
        """ Command sender. """

        while True:
            try:
                cmd = self._cmdqueue.get()

                ipaddr = cmd["target"]

                payload = None

                # get next sequence number if we haven't got one
                if "sequence" not in cmd:
                    cmd["sequence"] = self.get_sequence()

                payloadtype = cmd["payloadtype"]

                if payloadtype == PayloadType.SETCOLOR:
                    payload = self.gen_payload_setcolor(cmd["sequence"],
                                                        cmd["hue"],
                                                        cmd["sat"],
                                                        cmd["bri"],
                                                        cmd["kel"],
                                                        cmd["fade"])

                elif payloadtype == PayloadType.SETPOWER2:
                    payload = self.gen_payload_setpower(cmd["sequence"],
                                                        cmd["power"],
                                                        cmd["fade"])

                elif payloadtype == PayloadType.GET:
                    payload = self.gen_payload_get(cmd["sequence"])

                if payload is not None:
                    with self._send_lock:
                        cmd["sent"] = time.time()

                        try:
                            self._sock.sendto(payload, (ipaddr, UDP_PORT))

                            # set timeout
                            cmd["sent"] += ACK_TIMEOUT

                        # pylint: disable=broad-except
                        except Exception:
                            pass

                    with self._packet_lock:
                        self._packets.append(cmd)

            # pylint: disable=broad-except
            except Exception:
                pass

    def set_power(self, ipaddr, power, fade):
        """ Send setpower message. """
        cmd = {"payloadtype": PayloadType.SETPOWER2,
               "target": ipaddr,
               "power": power,
               "fade": fade}
        self._cmdqueue.put(cmd)

    def set_color(self, ipaddr, hue, sat, bri, kel, fade):
        """ Send setcolor message. """
        cmd = {"payloadtype": PayloadType.SETCOLOR,
               "target": ipaddr,
               "hue": hue,
               "sat": sat,
               "bri": bri,
               "kel": kel,
               "fade": fade}
        self._cmdqueue.put(cmd)
