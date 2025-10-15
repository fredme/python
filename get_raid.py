#!/usr/bin/python2
# -*- coding: utf-8 -*-

import subprocess
import json
import re
import getopt
import sys
import os
import glob

# color code
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
BOLD = '\033[1m'
END_COLOR = '\033[0m'

MOUNTED_RESULT = []


def run_command(command):
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            print("%srun command \"%s\" failed: return code %d, stderr: %s%s" % (RED, " ".join(command), process.returncode, stderr.decode('utf-8'), END_COLOR))
        return stdout.decode('utf-8')
    except Exception as e:
        print("%srun command \"%s\" error: %s%s" % (RED, " ".join(command), str(e), END_COLOR))
        return {}

# get lsi, MegaRAID or UNKNOWN
def get_lsi_card_type():
    try:
        lspci_output = run_command(['lspci'])
        megaraid_lines = [line for line in lspci_output.splitlines() if 'MegaRAID' in line]
        if megaraid_lines:
            return 'MegaRAID'
        else:
            return "UNKNOWN"
    except Exception as e:
        print("run command error: %s" % str(e))
        return "UNKNOWN"

# dell?
def is_dell():
    try:
        dmidecode_output = run_command(['/usr/sbin/dmidecode', '-t', 'system'])
        manufacturer_lines = [line for line in dmidecode_output.splitlines() if 'Manufacturer' in line]
        for line in manufacturer_lines:
            line = line.strip()
            if 'Dell' in line:
                return True
        return False
    except Exception as e:
        print("run command error: %s" % str(e))
        return False

# use /opt/MegaRAID/storcli/storcli64 or /opt/MegaRAID/perccli/perccli64 command to get megaraid info
def get_megaraid_info():
    if is_dell():
        megaraid_command = "/opt/MegaRAID/perccli/perccli64"
    else:
        megaraid_command = "/opt/MegaRAID/storcli/storcli64"

    vds_details = {"Controllers": []}
    try:
        vds_details_output = run_command([megaraid_command, '/call/vall', 'show', 'all', 'J'])
        vds_details = json.loads(vds_details_output)
    except (json.JSONDecodeError, ValueError, KeyError):
        return "run command error: %s" % str(e)
        
    vds_result = {}
    pds_result = {}

    # get vd info from vds_details
    for controller in vds_details["Controllers"]:
        if "Response Data" not in controller:
            continue
        for iterm in controller["Response Data"]:
            match_vd_code = re.search(r"^/c[0-9]+/v([0-9]+)$", iterm)
            if match_vd_code:
                cv_code = match_vd_code.group()  # /c0/v0
                v_code = match_vd_code.groups()[0]   # 0
                # every vd in controller["Response Data"][cv_code]
                vd = {}
                vd["type"] = controller["Response Data"][cv_code][0]["TYPE"] if "TYPE" in controller["Response Data"][cv_code][0] else ""  # RAID1
                vd["state"] = controller["Response Data"][cv_code][0]["State"] if "State" in controller["Response Data"][cv_code][0] else ""  # Optl
                vd["size"] = controller["Response Data"][cv_code][0]["Size"] if "Size" in controller["Response Data"][cv_code][0] else ""  # 278.875 GB
                vd["device name"] = controller["Response Data"]["VD"+v_code+" Properties"]["OS Drive Name"] if "OS Drive Name" in controller["Response Data"]["VD"+v_code+" Properties"] else ""
                vd["mountpoint"] = get_mounted_point(vd["device name"])
                tmp = controller["Response Data"]["VD"+v_code+" Properties"]["Span Depth"] if "Span Depth" in controller["Response Data"]["VD"+v_code+" Properties"] else "N/A"
                try:
                    vd["span depth"] = int(tmp)
                except ValueError:
                    vd["span depth"] = "N/A"
                tmp = controller["Response Data"]["VD"+v_code+" Properties"]["Number of Drives Per Span"] if "Number of Drives Per Span" in controller["Response Data"]["VD"+v_code+" Properties"] else "N/A"
                try:
                    vd["number of drives per span"] = int(tmp)
                except ValueError:
                    vd["number of drives per span"] = "N/A"
                vd["number of devices"] = vd["span depth"] * vd["number of drives per span"] if vd["span depth"] != "N/A" and vd["number of drives per span"] != "N/A" else "N/A"
                pds = []
                for pd_in_vd in controller["Response Data"]["PDs for VD "+v_code]:
                    pd = {}
                    slot = pd_in_vd["EID:Slt"] if "EID:Slt" in pd_in_vd else ""
                    pd["protocol"] = pd_in_vd["Intf"] if "Intf" in pd_in_vd else ""  # SAS
                    pd["media type"] = pd_in_vd["Med"] if "Med" in pd_in_vd else ""  # HDD
                    pd["size"] = short_size(pd_in_vd["Size"]) if "Size" in pd_in_vd else ""
                    pd["Media Error Count"] = -1  # 
                    pd["Other Error Count"] = -1  # 
                    pd["Predictive Failure Count"] = -1  # 
                    pd["state"] = pd_in_vd["State"] if "State" in pd_in_vd else "" # Onln
                    pd["SN"] = ""  # 
                    pds.append(slot)
                    # add pd to pds_result
                    pds_result[slot] = pd
                vd["pds"] = pds
                vds_result[cv_code] = vd

    # get pd info from pds_details
    pds_details = {"Controllers": []}
    try:
        pds_details_output = run_command([megaraid_command, '/call/eall/sall', 'show', 'all', 'J'])
        pds_details = json.loads(pds_details_output)
    except (ValueError, KeyError) as e:
        return vds_result, pds_result
        
    for controller in pds_details["Controllers"]:
        if "Response Data" not in controller:
            continue
        for iterm in controller["Response Data"]:
            match_drive_ces = re.search(r"^Drive (/c[0-9]+/e[0-9]+/s[0-9]+)$", iterm)
            if match_drive_ces:
                vd = {}
                ces = match_drive_ces.group() # Drive /c0/e32/s1
                # snum = match_drive_ces.groups()[0]   # 1
                ces_code = match_drive_ces.groups()[0]  # /c0/e32/s1
                slot = controller["Response Data"][ces][0]["EID:Slt"]
                # if slot in pds_result, update info and skip
                c = False
                for k in pds_result:
                    if slot == k:
                        pds_result[k]["Media Error Count"] = controller["Response Data"][ces+" - Detailed Information"][ces+" State"]["Media Error Count"] if "Media Error Count" in controller["Response Data"][ces+" - Detailed Information"][ces+" State"] else -1
                        pds_result[k]["Other Error Count"] = controller["Response Data"][ces+" - Detailed Information"][ces+" State"]["Other Error Count"] if "Other Error Count" in controller["Response Data"][ces+" - Detailed Information"][ces+" State"] else -1
                        pds_result[k]["Predictive Failure Count"] = controller["Response Data"][ces+" - Detailed Information"][ces+" State"]["Predictive Failure Count"] if "Predictive Failure Count" in controller["Response Data"][ces+" - Detailed Information"][ces+" State"] else -1
                        pds_result[k]["SN"] = short_sn(controller["Response Data"][ces+" - Detailed Information"][ces+" Device attributes"]["SN"]) if "SN" in controller["Response Data"][ces+" - Detailed Information"][ces+" Device attributes"] else ""
                        c = True
                        break
                if c:
                    continue

                # if slot not in vds_result, add it to vds_result
                vd["type"] = ""
                vd["state"] = ""
                vd["size"] = ""
                vd["device name"] = ""
                vd["mountpoint"] = ""
                vd["span depth"] = 1
                vd["number of drives per span"] = 1
                vd["number of devices"] = 1
                pds = []
                pd = {}
                #pd["slot"] = slot
                pd["protocol"] = controller["Response Data"][ces][0]["Intf"] if "Intf" in controller["Response Data"][ces][0] else ""  # SAS
                pd["media type"] = controller["Response Data"][ces][0]["Med"] if "Med" in controller["Response Data"][ces][0] else ""  # HDD
                pd["size"] = short_size(controller["Response Data"][ces][0]["Size"]) if "Size" in controller["Response Data"][ces][0] else ""  # 278.875 GB
                pd["Media Error Count"] = controller["Response Data"][ces+" - Detailed Information"][ces+" State"]["Media Error Count"] if "Media Error Count" in controller["Response Data"][ces+" - Detailed Information"][ces+" State"] else -1  # 0
                pd["Other Error Count"] = controller["Response Data"][ces+" - Detailed Information"][ces+" State"]["Other Error Count"] if "Other Error Count" in controller["Response Data"][ces+" - Detailed Information"][ces+" State"] else -1  # 0
                pd["Predictive Failure Count"] = controller["Response Data"][ces+" - Detailed Information"][ces+" State"]["Predictive Failure Count"] if "Predictive Failure Count" in controller["Response Data"][ces+" - Detailed Information"][ces+" State"] else -1  # 0
                pd["state"] = controller["Response Data"][ces][0]["State"] if "State" in controller["Response Data"][ces][0] else ""  # Onln
                pd["SN"] = short_sn(controller["Response Data"][ces+" - Detailed Information"][ces+" Device attributes"]["SN"]) if "SN" in controller["Response Data"][ces+" - Detailed Information"][ces+" Device attributes"] else ""
                pds.append(slot)
                vd["pds"] = pds
                pds_result[slot] = pd
                vds_result[ces_code] = vd
    return vds_result, pds_result


# Pass the device_name and return the first mount point if there are multiple partitions
def get_mounted_point(device_name):
    global MOUNTED_RESULT 
    if device_name == "":
        return ""
    if len(MOUNTED_RESULT) == 0:
        try:
            output = run_command("/bin/mount")
            MOUNTED_RESULT = output.strip().split('\n')
        except Exception:
            return ""
    for line in MOUNTED_RESULT:
        parts = line.split()
        if len(parts) >= 3:
            device_path = parts[0]
            if device_path == device_name or device_path.startswith(device_name):
                return parts[2]
    return ""


def short_size(size):
    sl = size.split()
    if len(sl) == 2:
        num, danwei = sl[0], sl[1]
        if danwei == "MB":
            num = int(num) / 1024.0
            danwei = "GB"
            if num > 1000:
                num = num / 1000
                danwei = "TB"
        if isinstance(num, str) and num.isdigit():
            return size
        num = float(num)
        if num > 100:
            num = int((num // 100 + 1) * 100)
        elif num > 10:
            num = int((num // 10 + 1) * 10)
        elif num > 1:
            num = int(num + 1)
        return str(num) + " " + danwei
    return size


def short_sn(sn):
    if sn == "":
        return ""
    s = sn.split()
    if len(s) == 3:
        if s[0] in ["SEAGATE", "TOSHIBA"]:
            return s[-1]
        if s[-1] in ["MFAOAB70", "HPG3", "D201DL13"]:
            return s[0]
        r = re.search(r'^WD-[\S]+', sn)
        if r:
            return r.group()
    if len(s) == 4:
        if s[1] in ["HGST"]:
            return s[0]
    if len(s) == 2:
        if len(s[-1]) < 5:
            return s[0]
    return sn

# add space to string to make it length max_len
def jiequ(s, max_len=12):
    tianchong = " "
    for i in range(max_len-1):
        tianchong += tianchong[0]

    if isinstance(s, int):
        s = str(s)
    if len(s) > max_len:
        return s[:max_len]
    return s + tianchong[:max_len-len(s)]


def my_cmp(my, nex):
    try:
        my_eid, my_slt = map(int, my.split(':'))
        nex_eid, nex_slt = map(int, nex.split(':'))
        
        if my_eid > nex_eid:
            return 1
        elif my_eid < nex_eid:
            return -1
        else:
            if my_slt > nex_slt:
                return 1
            elif my_slt < nex_slt:
                return -1
            else:
                return 0
    except (ValueError, IndexError):
        if len(my) > len(nex):
            return 1
        if len(my) == len(nex):
            if my > nex:
                return 1
            if my == nex:
                return 0
            return -1
        if len(my) < len(nex):
            return -1


def get_vd_k(slot, vds):
    for k in vds:
        vd = vds[k]
        for s in vd["pds"]:
            if s == slot:
                return k
    return ""

# return color code by state
def add_color_for_vd(state, p):
    if state == "Optl":
        return "%s%s%s" % (BLUE+BOLD, p, END_COLOR)
    elif state == "Dgrd":
        return "%s%s%s" % (RED+BOLD, p, END_COLOR)
    elif state == "":
        return "%s%s%s" % (YELLOW+BOLD, p, END_COLOR)
    else:
        return "%s%s%s" % (BOLD, p, END_COLOR)

def add_color_for_pd(state, p):
    if state == "Onln" or state == "DHS":
        return "%s%s%s" % (GREEN, p, END_COLOR)
    elif state == "Failed":
        return "%s%s%s" % (RED, p, END_COLOR)
    else:
        return "%s%s%s" % (YELLOW, p, END_COLOR)

# print vds and pds in human readable format
def print_human(vds, pds):
    vd_keys = ("type", "number of devices", "state", "size", "device name", "mountpoint")
    pd_keys = ("protocol", "media type", "state", "size", "Predictive Failure Count", "Media Error Count", "Other Error Count", "SN")

    # print title
    p = "%s " % jiequ("vd_k")
    for k in vd_keys:
        if k == "number of devices":
            p += "%s " % jiequ("%s(span*num of span)" % k)
        else:
            p += "%s " % jiequ(k)
    p = "%s%s%s" % (BOLD, p, END_COLOR)
    print(p)
    p = "%s " % jiequ(" # slot")
    for k in pd_keys:
        p += "%s " % jiequ(k)
    p = "%s%s%s" % (BOLD, p, END_COLOR)
    print(p)

    # print vds and pds in order of slot
    vds_k = []
    pds_k = sorted(pds.keys(), cmp=my_cmp)
    for slot in pds_k:
        vd_k = get_vd_k(slot, vds)
        if vd_k not in vds_k:
            vds_k.append(vd_k)

    for vd_k in vds_k:
        vd = vds[vd_k]
        # print vd
        p = "%s " % jiequ(vd_k)
        for k in vd_keys:
            if k == "number of devices":
                p += "%s " % jiequ("%s(%s*%s)" % (vd[k], vd["span depth"], vd["number of drives per span"]))
            else:
                p += "%s " % jiequ(vd[k])
        p = add_color_for_vd(vd["state"], p)
        print(p)
        # print pd in vd
        for slot in vd["pds"]:
            if slot not in pds:
                print("%s %s" % (jiequ(" # %s" % slot), "N/A"))
                continue
            pd = pds[slot]
            p = "%s " % jiequ(" # %s" % slot)
            for k in pd_keys:
                if k == "SN":
                    p += pd[k]
                    continue
                p += "%s " % jiequ(pd[k])
            
            p = "%s " % jiequ(" # %s" % slot)
            for k in pd_keys:
                if k == "state":
                    p += "%s " % jiequ(pd[k])
                elif k == "SN":
                    p += pd[k]
                    continue
                else:
                    p += "%s " % jiequ(pd[k])
            p = add_color_for_pd(pd["state"], p)
            print(p)

def usage():
    usa = """
    -h/--help: show help and exit.
    -j/--json: set json format.
"""
    print(usa)

if __name__ == "__main__":
    fmt = "human"
    options, args = getopt.getopt(sys.argv[1:], "hj", ["help", "json"])
    for name, value in options:
        if name in ("-h", "--help"):
            usage()
            sys.exit(0)
        if name in ("-j", "--json"):
            fmt = "json"
    
    lsi_card_type = get_lsi_card_type()
    if lsi_card_type != "MegaRAID":
        if fmt == "human":
            print("lsi not supported: %s" % lsi_card_type)
        else:
            print(json.dumps({"status": "error", "message": "lsi not supported: %s" % lsi_card_type}, indent=4))
        exit(1)

    vds, pds = get_megaraid_info()

    if fmt == "human":
        print_human(vds, pds)
    else:
        print(json.dumps({"status": "success", "vds": vds, "pds": pds}, indent=4))
