#!/usr/bin/env python3
"""Explicit physical test runner. LEDscape must already be stopped by operator.

Each case arms a fresh Saleae capture before invoking the native CLI test sender,
then checks exact counts and content. Stops at the first capture/CLI/check failure.
"""
import argparse
import datetime
import json
import pathlib
import re
import shlex
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def cases():
    result = []
    def add(name, lengths, color="123456", profile="ws2812b", count=3):
        result.append(dict(name=name, lengths=lengths, color=color, profile=profile, count=count))
    add("disabled", [0]*6, "FFFFFF")
    for index in range(6):
        for length in (1, 300):
            lens = [0]*6
            lens[index] = length
            add("pin%d-length%d" % (index,length), lens)
    for color in ("000000", "FFFFFF", "AAAAAA", "555555", "123456"):
        add("full-"+color, [300]*6, color, count=10)
    import itertools
    for permutation in itertools.permutations((1,2,3)):
        lens = [permutation[0],permutation[1],0,0,0,permutation[2]]
        add("bank2-"+"-".join(map(str,permutation)), lens)
    for permutation in ((1,1,3),(1,3,3)):
        add("bank2-tie-"+"-".join(map(str,permutation)), [permutation[0],permutation[1],0,0,0,permutation[2]])
    add("bank2-long-unequal", [300,180,0,0,0,1])
    for a,b in ((1,2),(2,1),(1,1),(1,300),(300,1)):
        add("bank1-%d-%d"%(a,b), [0,0,a,0,b,0])
    for index in (0,2,3):
        lens=[1]*6
        lens[index]=300
        add("longest-bank-pin%d"%index,lens)
    for mask in range(1,64):
        add("enable-mask-%02d"%mask, [int(bool(mask & (1<<i))) for i in range(6)], "AAAAAA")
    for index in (0,2,3):
        for length in (2,3,255,256,299):
            lens=[0]*6
            lens[index]=length
            add("counter-pin%d-length%d"%(index,length),lens,"555555")
    for profile in ("ws2812b","ws2811-hs","ws2812b-bgr","ws2811-hs-bgr"):
        for color in ("FF0000","00FF00","0000FF","123456"):
            add("profile-%s-%s"%(profile,color),[3]*6,color,profile)
    return result


def run_case(case, base, remote, ssh):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+",case["name"]):
        raise ValueError("unsafe case name")
    directory=base/case["name"]
    directory.mkdir()
    panel=directory/"panel.json"
    panel.write_text(json.dumps(dict(pixel_type=case["profile"],string_lengths=case["lengths"])))
    (directory/"case.json").write_text(json.dumps(case,indent=2))
    armed=directory/"armed.txt"
    stopped=directory/"stop-capture.txt"
    with (directory/"capture.log").open("w") as log:
        capture=subprocess.Popen([sys.executable,str(ROOT/"tools/bench_capture.py"),str(directory/"capture"),
            "--seconds","30","--label",case["name"],"--ready-file",str(armed),
            "--stop-file",str(stopped)],stdout=log,stderr=subprocess.STDOUT)
        remote_output=remote+"/"+base.name+"-"+case["name"]
        try:
            limit=time.monotonic()+15
            while not armed.exists():
                if capture.poll() is not None:
                    raise RuntimeError("capture failed to arm: "+str(directory))
                if time.monotonic()>limit:
                    raise RuntimeError("capture arm timeout: "+str(directory))
                time.sleep(0.02)
            argv=["python3",remote+"/tools/bench_sender.py","--build-dir",remote+"/build",
                  "--require-quiet",
                  "--output-dir",remote_output,"--profile",case["profile"],
                  "--lengths",",".join(map(str,case["lengths"])),"--mode","fixed","--color",case["color"],
                  "--count",str(case["count"]),"--deadline-utc","2026-09-06T13:17:00Z"]
            with (directory/"sender.log").open("w") as sender_log:
                send=subprocess.run(ssh+[" ".join(shlex.quote(a) for a in argv)],stdout=sender_log,stderr=subprocess.STDOUT,timeout=30)
            time.sleep(0.02)
            stopped.write_text(utc()+"\n")
            capture_code=capture.wait(timeout=60)
        except BaseException:
            stopped.write_text(utc()+"\n")
            try:
                capture.wait(timeout=15)
            except subprocess.TimeoutExpired:
                capture.terminate()
                try:
                    capture.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    capture.kill()
                    capture.wait(timeout=5)
            raise
    if capture_code or send.returncode:
        raise RuntimeError("capture exit=%s; sender exit=%s; artifacts=%s"%(capture_code,send.returncode,directory))
    fetched=subprocess.run(ssh+["cat "+shlex.quote(remote_output+"/summary.json")],capture_output=True,text=True,timeout=15,check=True)
    summary=json.loads(fetched.stdout)
    (directory/"sender-summary.json").write_text(json.dumps(summary,indent=2))
    if summary["successful_sends"] != case["count"] or summary["failed_sends"] or summary["exit"]:
        raise RuntimeError("sender did not complete exact requested count: "+str(directory))
    cmd=[sys.executable,str(ROOT/"tools/analyze_capture.py"),str(directory/"capture"),"--mode","dld",
         "--config",str(panel),"--colors",case["color"],"--bank-order","--expected-frames",str(case["count"]),
         "--json",str(directory/"analysis.json")]
    with (directory/"analysis.log").open("w") as log:
        analysis=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=120)
    report=json.loads((directory/"analysis.json").read_text())
    if analysis.returncode:
        raise RuntimeError("waveform check failed: %s, %s"%(directory,report.get("violation_counts",report.get("error"))))
    if any(c["partial_boundary_frames"] or c["partial_highs"] for c in report["channels"]):
        raise RuntimeError("unexpected boundary fragments in bracketed capture: "+str(directory))
    return {"case":case["name"],"status":"pass","coverage":report["coverage"],"duration_s":report["duration_s"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live",action="store_true",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--remote-dir",required=True)
    parser.add_argument("--known-hosts",required=True)
    parser.add_argument("--cases-json")
    args=parser.parse_args()
    if not re.fullmatch(r"/(root|run)/dld-[A-Za-z0-9_.-]+",args.remote_dir):
        parser.error("expected isolated /root/dld-NAME or RAM-backed /run/dld-NAME directory")
    base=pathlib.Path(args.output).resolve()
    base.mkdir(parents=True,exist_ok=False)
    selected=json.loads(pathlib.Path(args.cases_json).read_text()) if args.cases_json else cases()
    (base/"cases.json").write_text(json.dumps(selected,indent=2))
    ssh=["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10","-o","UserKnownHostsFile="+str(pathlib.Path(args.known_hosts).resolve()),"-o","StrictHostKeyChecking=yes","root@beaglebone"]
    state={"started_utc":utc(),"status":"running","total_cases":len(selected),"completed":[]}
    def checkpoint():
        temporary=base/"status.json.tmp"
        temporary.write_text(json.dumps(state,indent=2))
        temporary.replace(base/"status.json")
    try:
        for case in selected:
            if pathlib.Path(base/"STOP").exists():
                raise RuntimeError("operator stop file")
            state["current"]=case["name"]
            checkpoint()
            result=run_case(case,base,args.remote_dir,ssh)
            state["completed"].append(result)
            print(json.dumps(result),flush=True)
        state["status"]="pass"
    except BaseException as exc:
        state["status"]="fail"
        state["error"]=repr(exc)
        raise
    finally:
        state["updated_utc"]=utc()
        checkpoint()


if __name__ == "__main__":
    main()
