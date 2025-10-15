# python
python scripts list.

# get_raid.py
获取MegaRAID卡的RAID类型、RAID下的磁盘，以人类友好着色打印或以json的格式打印。

## 前提
dell服务器需安装/opt/MegaRAID/perccli/perccli64；
其他服务器（如浪潮）需安装/opt/MegaRAID/storcli/storcli64。

## 使用说明
在centos7系统下测试通过。

```shell
./get_raid.py -h

    -h/--help: show help and exit.
    -j/--json: set json format.

```

