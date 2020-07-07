#!/usr/bin/env python3

from collections import defaultdict
import sys
import time
import glob
import argparse
import libevdev

class XBoxController():

    def __init__(self, file, devkb):
        self.devjs = libevdev.Device()
        self.mapping = self.parseFile(file, devkb, self.devjs)
        self.value = defaultdict(int)

    def parseFile(self, path, devkb, devjs):
        # create a mapping from keyboard (kb) -> joystick (js) buttons
        # kb event code -> js event code
        # kb event code, kb event value -> js event value
        mapping = dict()          # ^ 0: released, 1: pressed, 2: repeat call. see EV_KEY doc

        # create the xbox controller buttons and axes
        id = dict()
        with open(path) as fp:
            for x in fp:
                if len(x) <= 1 or x[0] == "#":
                    continue
                var, val = x.strip().split("=")
                if var == "NAME":
                    devjs.name = val
                elif var == "VENDOR" or var == "PRODUCT" or var == "VERSION":
                    id[var.lower()] = int(val, 0)
                else:
                    evjs = libevdev.evbit(var)
                    if evjs is not None and evjs.is_defined:
                        ai = libevdev.InputAbsInfo(
                            minimum=-1000, maximum=1000,
                            ) if evjs.type.name == "EV_ABS" else None
                        devjs.enable(evjs, ai)

                        # create the table
                        keys = val.split(",")
                        for i,k in enumerate(keys):
                            evkb = libevdev.evbit(k)
                            if evkb is not None and evkb.is_defined and devkb.has(evkb):
                                mapping[evkb] = evjs
                                mapping[evkb, 0] = 0
                                mapping[evkb, 1] = 1
                                if evjs.type == libevdev.EV_ABS:
                                    steps = (ai.maximum - ai.minimum) // len(keys)
                                    if i >= len(keys) // 2:
                                        i += 1
                                    mapping[evkb, 1] = ai.minimum + steps * i
                                print("mapping {} -> {}, {}".format(evkb.name, evjs.name, mapping[evkb, 1]))
        devjs.id = id
        return mapping

    def create(self):
        self.uinput = self.devjs.create_uinput_device()
        print('Device {} is at {}'.format(self.devjs.name, self.uinput.devnode))

    def fire(self, e):
        if e.code in self.mapping:
            if e.value == 2: # repeat code
                return
            # if we want to release an EV_ABS, i.e. set to 0, then check if the value was not changed to the other direction
            # if that is the case, do not set it back to 0
            if self.mapping[e.code].type == libevdev.EV_ABS and e.value == 0:
                # self.devjs.value SHOULD work the same way according to doc, but it doesnt
                if self.mapping[e.code, 1] != self.value[self.mapping[e.code]]:
                    return
            print("got {}, status is {}. send {} with value {}."
                .format(e.code, e.value, self.mapping[e.code].name, self.mapping[e.code, e.value]))
            events = libevdev.InputEvent(self.mapping[e.code], self.mapping[e.code, e.value])
            self.uinput.send_events([events, libevdev.InputEvent(libevdev.EV_SYN.SYN_REPORT, 0)])
            self.value[self.mapping[e.code]] = self.mapping[e.code, e.value]


def printKeyboards(args):
    CHECKS = [
        libevdev.EV_KEY.KEY_P,
        libevdev.EV_KEY.KEY_Y,
        libevdev.EV_KEY.KEY_K,
        libevdev.EV_KEY.KEY_E,
        libevdev.EV_KEY.KEY_N,
        ]
    print("Finding all available keyboards...")
    for f in glob.glob('/dev/input/event*'):
        with open(f, 'rb') as fd:
            dev = libevdev.Device(fd)
            passed = True
            for c in CHECKS:
                if not dev.has(c):
                    passed = False
                    break
            if passed:
                print()
                print("DEVICE: {}".format(f))
                print("  Name: {}".format(dev.name))
                print("    ID: bus {:#x} vendor {:#x} product {:#x} version {:#x}"
                    .format(dev.id["bustype"], dev.id["vendor"], dev.id["product"], dev.id["version"] ))
                v = dev.driver_version
                print("        Input driver version is {}.{}.{}".format(v >> 16, (v >> 8) & 0xff, v & 0xff))


def anyKeyPressed(fd):
    dev = libevdev.Device(fd)
    for c in dev.evbits[libevdev.EV_KEY]:
        if dev.value[c] != 0:
            return True
    return False


def main(args):

    if "-l" in args or "--list" in args:
        printKeyboards(args)
        return 0

    parser = argparse.ArgumentParser(description='Convert a keyboard to (multiple) xbox controllers.')
    parser.add_argument('configs', metavar='CONFIGS', type=str, nargs='+',
                        help='One or more configuration files')
    parser.add_argument('-d', '--device', dest='device', type=str, required=True,
                        help='Input event file of keyboard, usually under /dev/input')
    parser.add_argument('-l', '--list', dest='listkbds', action='store_const', const=True, default=False,
                        help='List available keyboards')
    argv = parser.parse_args()

    #if argv.listkbds:
    #    printKeyboards(args)
    #    return 0

    
    # prepare reading keyboard device
    path = argv.device
    fd = open(path, 'rb')
    # wait until all keys are released (sometimes Enter is still pressed down when this script is executed)
    while anyKeyPressed(fd):
        time.sleep(0.01)
    devkb = libevdev.Device(fd)

    # prepare writing device
    controllers = list()
    for f in argv.configs:
        controllers.append(XBoxController(f, devkb))
        print()
    try:
        for c in controllers:
            c.create()
    except OSError as e:
        print(e)
        return 1

    print()
    print("Press Ctrl+F1 to toggle grabbing the keyboard.")
    print("Press Ctrl+Escape to quit.")
    print()

    devkb.grab()
    grab = True
    leftctrl = False
    f1 = False
    doGrabChangeReached = False

    run = True
    while run:
        for e in devkb.events():
            if grab:
                for c in controllers:
                    c.fire(e)
            if e.code == libevdev.EV_KEY.KEY_LEFTCTRL:
                leftctrl = False if e.value == 0 else True
                if not leftctrl and not f1 and doGrabChangeReached:
                    doGrabChangeReached = False
                    grab = not grab
                    print("Received Ctrl+F1. Set grab to {}.".format(grab))
                    devkb.grab() if grab else devkb.ungrab()
            elif e.code == libevdev.EV_KEY.KEY_ESC:
                if e.value == 1 and leftctrl:
                    print("Received Ctrl+Escape. Exit.")
                    run = False
                    break
            elif e.code == libevdev.EV_KEY.KEY_F1:
                f1 = False if e.value == 0 else True
                if f1 and leftctrl:
                    doGrabChangeReached = True
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))

