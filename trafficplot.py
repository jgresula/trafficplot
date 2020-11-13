#!/usr/bin/env python3
import subprocess
import re
from collections import namedtuple
import time
import atexit
import argparse
import tempfile
import os
import sys
from pathlib import Path


plot_data = Path('/dev/shm/trafficplot-{}.dat'.format(os.getpid()))
plot_data_tmp = Path('/dev/shm/trafficplot-tmp-{}.dat'.format(os.getpid()))

def cleanup():
    for path in [plot_data, plot_data_tmp]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
atexit.register(cleanup)

# https://stackoverflow.com/a/1094933        
def sizeof_fmt(num, suffix='B', prefix=''):
    for unit in ['','K','M','G','T','P','E','Z']:
        if abs(num) < 1000:
            return "%3.1f%s%s%s" % (num, prefix, unit, suffix)
        num /= 1000
    return "%.1f%s%s%s" % (num, prefix, 'Y', suffix)


Bytes = namedtuple('Bytes', ['rx', 'tx'])
Bandwidth = namedtuple('Bandwidth', ['rx', 'tx'])


def get_iface_lines():
    if args.remote:
        # -t -x kills the remote process when ssh session is done
        cmd = ["ssh", "-t", "-x", args.remote,
               'while true; do ifconfig {}; sleep {} ; done'.format(
                   args.iface, args.interval)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, **args.popen_args_ex)
        atexit.register(proc.terminate)
        for line in iter(proc.stdout.readline, ''):
            yield line.decode('ascii')
    else:
        while True:
            r = subprocess.run(["ifconfig", args.iface],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
            for line in r.stdout.decode('ascii').splitlines():
                yield line
            time.sleep(args.interval)
            

rexes = [
    re.compile("RX bytes:(?P<rx>[0-9]+).+TX bytes:(?P<tx>[0-9]+)"),
    re.compile("RX .+ bytes (?P<rx>[0-9]+)"),
    re.compile("TX .+ bytes (?P<tx>[0-9]+)"),
]
# parses ifconfig output, yields rx/tx bytes
class Parser:
    def __init__(self):
        self.rx = None
        self.tx = None

    def parse(self):
        for line in get_iface_lines():
            self.push(line)
            if self.rx and self.tx:
                yield Bytes(self.rx, self.tx)
                self.rx = self.tx = None
                
    def push(self, line):
        for rex in rexes:
            m = rex.search(line)
            if m:
                d = m.groupdict(0)
                self.rx = self.rx or int(d.get('rx', 0))
                self.tx = self.tx or int(d.get('tx', 0))
                break


# collects rx/tx bytes and maintains an array of recent rx/tx speeds
class Collector:
    def __init__(self):
        self.last_rx = -1
        self.last_tx = -1
        self.data = args.num_samples * [Bandwidth(0, 0)]
        self.last_ts = None

    def add_bytes(self, bytes):
        now = time.time()        
        if self.last_rx >= 0:
            delta_rx = bytes.rx - self.last_rx
            delta_tx = bytes.tx - self.last_tx
            delta_t = now - self.last_ts            
        else:
            delta_rx, delta_tx = 0, 0
            delta_t = 1
        self.last_ts = now
        self.last_rx = bytes.rx
        self.last_tx = bytes.tx
        self.data = self.data[1:] + [Bandwidth(delta_rx//delta_t, delta_tx//delta_t)]

    def write_plot_file(self):
        seconds = range(args.interval*(len(self.data)-1), -1, -args.interval)
        down = sizeof_fmt(self.data[-1].rx*8, suffix='bps')
        up = sizeof_fmt(self.data[-1].tx*8, suffix='bps')        
        with open(plot_data_tmp, 'w') as fd:
            fd.write('"{}" "{} Down" "{} Up  "\n'.format('time', down, up))
            for second, bandwidth in zip(seconds, self.data):
                rx_bps = bandwidth.rx * 8 / 1000000
                tx_bps = bandwidth.tx * 8 / 1000000
                fd.write("{} {} {}\n".format(second, rx_bps, tx_bps))
        plot_data_tmp.rename(plot_data)
                


plot_script_template = """
set term {terminal} {termopts} size {width},{height}

set xlabel "Seconds ago"
set xrange [{max_xrange}:0]
set grid xtics

set autoscale y
set ylabel "Mbps"
set grid ytics

set key autotitle columnhead
set key opaque

plot "{plot_data}" using 1:2 with lines, \
     "{plot_data}" using 1:3 with lines

bind "Close" exit
pause {interval}
reread
"""


def write_plot_script(fd):
    tvars = {
        'plot_data': plot_data,
        'interval': args.interval,
        'max_xrange': args.interval*(args.num_samples-1),
        'width': args.width,
        'height': args.height,
        'iface': args.iface,
        'terminal': args.terminal,
        'termopts': '',
    }
    if args.remote:
        tvars['iface'] = "{} {}".format(args.remote, tvars['iface'])
    if args.terminal == 'dumb':
        tvars['termopts'] = 'ansi256'
    else:
        tvars['termopts'] = '1 noraise title "Traffic - {iface}"'.format(**tvars)
    fd.write(plot_script_template.format(**tvars))
    fd.flush()


def try_daemonize():
    if args.daemonize and not args.debug and args.terminal!='dumb':
        if os.fork():
            sys.exit(0)
    

def main():
    try_daemonize()    
    coll = Collector()
    coll.write_plot_file() # first empty run
    # run gnuplot
    with tempfile.NamedTemporaryFile("w") as fd:
        write_plot_script(fd)
        proc = subprocess.Popen(['gnuplot', fd.name], **args.popen_args_ex)
        atexit.register(proc.terminate)
        # forever loop
        parser = Parser()
        for bytes in parser.parse():
            coll.add_bytes(bytes)
            coll.write_plot_file()
            if proc.poll() != None:
                break

            
def parse_args():
    parser = argparse.ArgumentParser(description='Realtime traffic chart.')
    parser.add_argument('-i', '--iface', required=True, help='network interface')
    parser.add_argument('-d', '--daemonize', action="store_true",
                        help='deamonize if not debugging or not dumb terminal output')
    parser.add_argument('-r', '--remote',
                        help='user@hostname (via ssh)')
    parser.add_argument('-n', '--num-samples', default=120, type=int,
                        help='number of samples to show')
    parser.add_argument('-e', '--interval', default=1, type=int,
                        help='interval between samples in seconds')
    parser.add_argument('-t', '--terminal', default='x11',
                        choices=('x11', 'wxt', 'dumb'),
                        help='gnuplot terminal (x11, wxt or dumb)')
    parser.add_argument('-W', '--width', default=0, type=int,
                        help='terminal width')
    parser.add_argument('-H', '--height', default=0, type=int,
                        help='terminal height')
    parser.add_argument('-v', '--debug', action="store_true",
                        help='enable stderr (it goes to /dev/null by default)')
    args = parser.parse_args()
    if args.terminal == 'dumb':
        args.width = args.width or 80
        args.height = args.height or 20
    else:
        args.width = args.width or 1024
        args.height = args.height or 512
    if args.debug:    
        args.popen_args_ex = {}
    else:
        args.popen_args_ex = dict(stderr=subprocess.DEVNULL)
    return args
            

if __name__ == "__main__":
    try:
        args = parse_args()
        main()
    except KeyboardInterrupt:
        pass
