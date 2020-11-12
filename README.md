# trafficplot.py
Plots a realtime network traffic chart. The data is parsed from `ifconfig` output. Remote hosts are supported via ssh and the `--hostname` parameter.

Examples:
```
trafficplot.py -i eth0
trafficplot.py -i vlan2 --remote=root@dd-wrt -t=dumb
```

Requirements:
- linux
- python 3.6+
- gnuplot (present in most linux distros)

License: MIT

### Screenshots

![X11 Terminal](/screens/x11.png?raw=true "X11 Terminal")

![Dumb Terminal](/screens/dumb.png?raw=true "Dumb Terminal")



