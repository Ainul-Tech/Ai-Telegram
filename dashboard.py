"""
dashboard.py — Dashboard web untuk melihat riwayat.

Jalankan terpisah dari bot:
    python dashboard.py
Lalu buka http://127.0.0.1:8080

Data ditarik dari Bybit (catatan eksekusi buy/sell + closed PnL) dan
disimpan ke SQLite, jadi angkanya berasal dari bursa, bukan asumsi bot.
Tombol "Sync" di dashboard memicu penarikan ulang.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os

from flask import Flask, jsonify, request

from bybit_client import BybitClient
from config import cfg
from history import History

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
hist = History(cfg.DB_PATH)
client = BybitClient(cfg.bybit_key, cfg.bybit_secret, testnet=cfg.testnet)


def fmt_ts(ms) -> str:
    if not ms:
        return "-"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


PAGE = """<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0e1117;--card:#161b22;--line:#232a34;--tx:#e6edf3;--mut:#8b949e;
--up:#3fb950;--dn:#f85149;--acc:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:20px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .lbl{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.card .val{font-size:22px;font-weight:600;margin-top:4px}
.up{color:var(--up)} .dn{color:var(--dn)}
.tabs{display:flex;gap:6px;margin:20px 0 12px;flex-wrap:wrap}
.tab{padding:7px 14px;background:var(--card);border:1px solid var(--line);
border-radius:7px;cursor:pointer;color:var(--mut)}
.tab.on{color:var(--tx);border-color:var(--acc)}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:9px 11px;text-align:left;border-bottom:1px solid var(--line);
font-size:13px;white-space:nowrap}
th{color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase}
tr:last-child td{border-bottom:none}
.scroll{overflow-x:auto}
button{background:var(--acc);color:#04121f;border:0;padding:8px 16px;
border-radius:7px;font-weight:600;cursor:pointer}
.hide{display:none}
.badge{padding:2px 8px;border-radius:5px;font-size:11px;background:#21262d;color:var(--mut)}
canvas{max-height:260px}
.warn{background:#3d1d1d;border:1px solid var(--dn);color:#ffb4ae;
padding:10px 14px;border-radius:8px;margin-bottom:16px}
</style></head><body><div class="wrap">
<h1>Telegram → Bybit Bot</h1>
<div class="sub" id="mode"></div>
<div id="halted"></div>

<div class="grid" id="stats"></div>
<div class="card" style="margin-bottom:20px"><canvas id="chart"></canvas></div>

<div class="tabs">
  <div class="tab on" data-t="pnl">Closed PnL</div>
  <div class="tab" data-t="exec">Eksekusi Buy/Sell</div>
  <div class="tab" data-t="sig">Sinyal Masuk</div>
  <button style="margin-left:auto" onclick="sync()">Sync dari Bybit</button>
</div>
<div class="scroll"><div id="pnl"></div><div id="exec" class="hide"></div>
<div id="sig" class="hide"></div></div>
</div>
<script>
let chart;
function tbl(rows, cols){
  if(!rows.length) return '<table><tr><td style="color:#8b949e">Belum ada data.</td></tr></table>';
  let h='<table><thead><tr>'+cols.map(c=>'<th>'+c[0]+'</th>').join('')+'</tr></thead><tbody>';
  for(const r of rows){h+='<tr>'+cols.map(c=>'<td>'+c[1](r)+'</td>').join('')+'</tr>';}
  return h+'</tbody></table>';
}
const money=v=>{const n=Number(v)||0;const c=n>0?'up':(n<0?'dn':'');
  return '<span class="'+c+'">'+(n>0?'+':'')+n.toFixed(4)+'</span>';}
async function load(){
  const d=await (await fetch('/api/data')).json();
  document.getElementById('mode').textContent =
    (d.testnet?'TESTNET':'LIVE — uang sungguhan')+' · leverage '+d.leverage+'x · entry '+
    d.entry_pct+'% equity · equity awal '+Number(d.start_equity).toFixed(2)+
    ' · equity kini '+Number(d.equity).toFixed(2)+' USDT';
  document.getElementById('halted').innerHTML = d.halted
    ? '<div class="warn">BOT BERHENTI — batas drawdown '+d.max_dd+'% tercapai.</div>' : '';
  const s=d.stats;
  document.getElementById('stats').innerHTML=[
    ['Total Trade',s.trades],
    ['Win Rate',s.win_rate.toFixed(1)+'%'],
    ['Menang / Kalah',s.wins+' / '+s.losses],
    ['Total PnL',money(s.total_pnl)],
    ['Rata Menang',money(s.avg_win)],
    ['Rata Kalah',money(s.avg_loss)],
    ['Drawdown',d.drawdown.toFixed(1)+'%'],
  ].map(x=>'<div class="card"><div class="lbl">'+x[0]+'</div><div class="val">'+x[1]+'</div></div>').join('');

  document.getElementById('pnl').innerHTML=tbl(d.pnl,[
    ['Waktu',r=>r.ts_h],['Symbol',r=>r.symbol],['Sisi',r=>r.side],
    ['Qty',r=>r.qty],['Entry',r=>r.avg_entry],['Exit',r=>r.avg_exit],
    ['Lev',r=>r.leverage+'x'],['PnL',r=>money(r.pnl)]]);
  document.getElementById('exec').innerHTML=tbl(d.exec,[
    ['Waktu',r=>r.ts_h],['Symbol',r=>r.symbol],['Sisi',r=>r.side],
    ['Harga',r=>r.price],['Qty',r=>r.qty],['Fee',r=>r.fee],
    ['Tipe',r=>'<span class="badge">'+(r.exec_type||'-')+'</span>'],
    ['Tag',r=>r.order_link_id||'-']]);
  document.getElementById('sig').innerHTML=tbl(d.signals,[
    ['Waktu',r=>r.ts_h],['Channel',r=>r.channel||'-'],['Symbol',r=>r.symbol],
    ['Sisi',r=>r.side],['Entry',r=>r.entries],['TP',r=>r.take_profits],
    ['SL',r=>r.stop_loss],['Lev sinyal',r=>(r.raw_leverage||'-')+'x'],
    ['Lev dipakai',r=>(r.used_leverage||'-')+'x'],
    ['Status',r=>'<span class="badge">'+r.status+'</span>'],['Catatan',r=>r.note||'']]);

  const c=d.curve;
  if(chart) chart.destroy();
  chart=new Chart(document.getElementById('chart'),{type:'line',
    data:{labels:c.map(p=>p.ts_h),datasets:[{label:'PnL kumulatif (USDT)',
      data:c.map(p=>p.cum_pnl),borderColor:'#58a6ff',
      backgroundColor:'rgba(88,166,255,.12)',fill:true,tension:.25,pointRadius:0}]},
    options:{responsive:true,plugins:{legend:{labels:{color:'#8b949e'}}},
      scales:{x:{ticks:{color:'#8b949e',maxTicksLimit:8},grid:{color:'#232a34'}},
              y:{ticks:{color:'#8b949e'},grid:{color:'#232a34'}}}}});
}
async function sync(){await fetch('/api/sync',{method:'POST'});load();}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  t.classList.add('on');
  ['pnl','exec','sig'].forEach(id=>document.getElementById(id).classList.add('hide'));
  document.getElementById(t.dataset.t).classList.remove('hide');});
load(); setInterval(load, 30000);
</script></body></html>"""


@app.route("/")
def index():
    return PAGE


@app.route("/api/data")
def api_data():
    pnl = hist.recent_pnl(300)
    execs = hist.recent_executions(300)
    sigs = hist.recent_signals(200)
    curve = hist.equity_curve()
    for r in pnl + execs + sigs:
        r["ts_h"] = fmt_ts(r.get("ts"))
    for p in curve:
        p["ts_h"] = fmt_ts(p["ts"])
    for s in sigs:
        for k in ("entries", "take_profits"):
            try:
                s[k] = ", ".join(str(x) for x in json.loads(s[k] or "[]"))
            except Exception:
                pass
        s.pop("raw_text", None)

    start_eq = float(hist.get_meta("start_equity", 0) or 0)
    try:
        eq = client.equity_usdt()
    except Exception:
        eq = 0.0
    dd = ((start_eq - eq) / start_eq * 100) if start_eq > 0 else 0.0

    return jsonify({
        "stats": hist.stats(),
        "pnl": pnl, "exec": execs, "signals": sigs, "curve": curve,
        "testnet": cfg.testnet, "leverage": cfg.LEVERAGE,
        "entry_pct": cfg.ENTRY_EQUITY_PERCENT,
        "start_equity": start_eq, "equity": eq,
        "drawdown": dd, "max_dd": cfg.MAX_DRAWDOWN_PERCENT,
        "halted": bool(hist.get_meta("halted_at")),
    })


@app.route("/api/sync", methods=["POST"])
def api_sync():
    days = int(request.args.get("days", 7))
    n_exec, n_pnl = hist.sync_from_bybit(client, lookback_days=days)
    return jsonify({"executions": n_exec, "closed_pnl": n_pnl})


if __name__ == "__main__":
    hist.sync_from_bybit(client, lookback_days=7)
    print(f"Dashboard: http://{cfg.DASH_HOST}:{cfg.DASH_PORT}")
    host = "0.0.0.0" if os.getenv("PORT") else cfg.DASH_HOST
    app.run(host=host, port=cfg.DASH_PORT, debug=False)
