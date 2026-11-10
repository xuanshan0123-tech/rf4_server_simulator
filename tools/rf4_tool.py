#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RF4 私服 · 扩展管理工具
纯标准库，单文件，监听 127.0.0.1:9600

功能：
  1. 商店批量改价（全店银币化 / 纯金币转银币 / 乘系数 / 改库存 / 单行编辑）
  2. 参数管理（含 condition_* 体力咖啡等未在官方后台暴露的键）
  3. 鱼口覆盖监测（每图鱼种数、点位数、样本量，标出偏薄的图）
"""
import json
import os
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "rf4server.db")
STATIC = os.path.join(ROOT, "server_data", "static.sqlite3")
HOST, PORT = "127.0.0.1", 9600

# 参数定义：key -> (标签, 说明, 建议最小值, 建议最大值)
TUNABLES = {
    "fight_power_scale": ("搏斗拉力倍率", "越低鱼越弱；1=原版", 0.1, 3),
    "fish_stamina_scale": ("鱼耐力倍率", "越高鱼越耐拉，搏斗更久", 0.2, 3),
    "condition_pull_energy_cost": ("拉鱼体力消耗", "每次拉扯扣多少体力（0.004=0.4%）", 0, 0.05),
    "condition_cast_energy_cost": ("抛竿体力消耗", "每次抛竿扣多少体力", 0, 0.05),
    "condition_coffee_energy_restore": ("咖啡回复体力", "每次喝咖啡回复量（0.12=12%）", 0, 1),
    "condition_cocoa_energy_restore": ("可可回复体力", "每次喝可可回复量", 0, 1),
    "condition_energy_regen_per_hour": ("体力自然回复/小时", "不活动时每小时回复", 0, 1),
    "condition_hunger_decay_per_hour": ("饥饿下降/小时", "越高越容易饿", 0, 1),
    "condition_health_decay_per_hour": ("健康下降/小时", "越低越难掉健康", 0, 1),
    "condition_health_regen_per_hour": ("健康回复/小时", "健康自然恢复速度", 0, 1),
    "condition_energy_max": ("体力上限", "体力条上限", 0.1, 1),
    "condition_low_energy_threshold": ("低体力阈值", "低于此值进入低体力状态", 0, 1),
    "bite_delay_scale": ("咬钩延迟倍率", "越小咬钩越快", 0.05, 3),
    "bite_delay_max": ("咬钩上限秒", "最长等待", 1, 120),
    "fish_weight_scale": ("鱼体重倍率", "整体放大鱼体重", 0.2, 5),
    "xp_gain": ("经验倍率", "结算经验缩放", 0.1, 10),
    "electric_locked_multiplier": ("电轮锁轮倍率", "锁轮承力=较小者×该倍率；默认3", 1, 5),
    "electric_motor_kg_per_power": ("电轮电机功率折算", "电机功率→公斤", 0.2, 5),
    "fish_escape_taunt": ("跑鱼提示", "1=嘲讽文案 0=官方模板", 0, 1),
    "loot_box_enabled": ("钓获礼包", "1=按概率开礼包", 0, 1),
    "loot_box_chance": ("礼包概率", "0~1", 0, 1),
}


def db(path=DB):
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    return con


# 地图名（level key -> 中文）
LEVEL_ZH = {
    "level_001_lake": "克马羚诺也湖", "level_002_kirzah_river": "惟有诺克河",
    "level_003_viiveri_lake": "廓里湖", "level_004_torfyanoe_ozero": "旧奥斯特罗格湖",
    "level_005_volhov": "沃尔霍夫河", "level_006_ladoga": "拉多加湖",
    "level_007_grass_lake": "梅德韦杰湖", "level_008_sura_river": "苏拉河",
    "level_009_ahtuba": "阿赫图巴河", "level_010_belaya": "白河",
    "level_011_ladoga_02": "拉多加湖群岛", "level_012_north_sea": "挪威海",
    "level_013_sev_don": "北顿涅茨河", "level_014_carp_lake": "琥珀湖",
    "level_015_tunguska": "下通古斯卡河", "level_016_yama": "亚马河",
    "level_018_moskitnoe": "铜湖", "level_019_american_pond": "埃尔克湖",
}

# 商店类型 -> 中文（按 store_id 前缀）
STORE_KIND = {
    "fishing_store": "钓鱼商店", "fishingstore": "钓鱼商店", "fishstore": "钓鱼商店",
    "craft_baits": "饵料工坊", "craft_lures": "拟饵工坊",
    "food_store": "食品店", "foodstore": "食品店",
    "tool_store": "工具店", "workshop": "工坊", "atm": "取款机",
    "boatstation": "船站", "bakery": "面包房", "mill": "磨坊", "varyag": "瓦良格商店",
    "vakula": "瓦库拉商店", "paseka": "养蜂场", "sawmill": "锯木厂", "milkstore": "奶站",
    "farmstore": "农庄", "kala": "卡利亚商店", "arbuz": "阿尔布兹", "tungus": "通古斯商店",
    "monkeycola": "猴可乐", "housestore": "家居店", "zeiman": "泽曼",
    "ny_bazaar": "集市", "tours": "观光处", "rentbike": "租车处", "cafestore": "咖啡店",
    "belugastore": "白鲸商店", "belugaworkshop": "白鲸工坊", "arm": "军火库",
    "admin": "管理处", "level_004_admin": "管理处",
}

# 某些特殊 store_id 直接写死
STORE_FIXED = {
    "level_004_admin": "管理处", "level_004_kvas": "克瓦斯店", "level_004_atm": "取款机",
    "level_004_fishingshop": "钓鱼商店", "level_004_foodshop": "食品店", "level_004_workshop": "工坊",
}

# 编号后缀 -> level key
_NUM2LEVEL = {
    "01": "level_001_lake", "02": "level_002_kirzah_river", "003": "level_003_viiveri_lake",
    "004": "level_004_torfyanoe_ozero", "005": "level_005_volhov", "006": "level_006_ladoga",
    "007": "level_007_grass_lake", "008": "level_008_sura_river", "009": "level_009_ahtuba",
    "010": "level_010_belaya", "011": "level_011_ladoga_02", "012": "level_012_north_sea",
    "013": "level_013_sev_don", "14": "level_014_carp_lake", "015": "level_015_tunguska",
    "016": "level_016_yama", "18": "level_018_moskitnoe", "19": "level_019_american_pond",
    "viiveri": "level_003_viiveri_lake", "volhov": "level_005_volhov",
    "sura": "level_008_sura_river", "grass": "level_007_grass_lake",
    "garage": "level_005_volhov", "isle": "level_005_volhov",
}


def store_zh(store_id):
    if store_id in STORE_FIXED:
        return STORE_FIXED[store_id]
    sid = store_id.lower()
    # 商店类型（前缀匹配，取最长的命中）
    kind_zh = None
    for kind, zh in sorted(STORE_KIND.items(), key=lambda x: -len(x[0])):
        if sid.startswith(kind):
            kind_zh = zh
            tail = sid[len(kind):].strip("_")
            break
    # 地图（优先 spot_def，其次编号后缀）
    lz = None
    try:
        con = sqlite3.connect(DB, timeout=5)
        row = con.execute("SELECT map_key FROM spot_def WHERE system_id=?", (store_id,)).fetchone()
        con.close()
        if row and row[0] in LEVEL_ZH:
            lz = LEVEL_ZH[row[0]]
    except Exception:
        pass
    if lz is None and kind_zh:
        tail = sid[len(kind_zh) if kind_zh else 0:]
        # 重新算 tail
        for kind in STORE_KIND:
            if sid.startswith(kind):
                tail = sid[len(kind):].strip("_")
                lk = _NUM2LEVEL.get(tail)
                if lk:
                    lz = LEVEL_ZH[lk]
                break
    if kind_zh and lz:
        return f"{kind_zh} · {lz}"
    if kind_zh:
        return kind_zh
    if lz:
        return lz
    return store_id


def ok(**kw):
    kw.setdefault("ok", True)
    return kw


# ------------------------------ 商店 ------------------------------

def stores():
    con = db()
    rows = con.execute("""SELECT store_id, COUNT(*) n, SUM(stock) st,
                                 SUM(silver>0 AND gold>0) both_g,
                                 SUM(silver=0 AND gold>0) gold_only,
                                 SUM(silver=0 AND gold=0) zero
                          FROM shop_item GROUP BY store_id ORDER BY n DESC""").fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["name"] = store_zh(d["store_id"])
        out.append(d)
    return out


def items(store, q="", limit=300):
    con = db()
    if store:
        # 指定商店：搜该店
        sql = "SELECT store_id, item_id, silver, gold, stock FROM shop_item WHERE store_id=?"
        args = [store]
        if q:
            sql += " AND (item_id LIKE ? OR ? IN (SELECT name_zh FROM item_catalog WHERE item_id=shop_item.item_id))"
            args.append(f"%{q}%")
            args.append(q)
        sql += " ORDER BY item_id LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in con.execute(sql, args)]
    else:
        # 全局搜索：跨所有商店
        if q:
            sql = """SELECT store_id, item_id, silver, gold, stock FROM shop_item
                     WHERE item_id LIKE ? OR item_id IN (SELECT item_id FROM item_catalog WHERE name_zh LIKE ?)
                     ORDER BY item_id, store_id LIMIT ?"""
            args = [f"%{q}%", f"%{q}%", limit]
        else:
            sql = "SELECT store_id, item_id, silver, gold, stock FROM shop_item ORDER BY item_id, store_id LIMIT ?"
            args = [limit]
        rows = [dict(r) for r in con.execute(sql, args)]
    # 补充中文名 + 商店中文名
    if rows:
        ids = list(dict.fromkeys(r["item_id"] for r in rows))
        ph = ",".join("?" * len(ids))
        names = {}
        try:
            for iid, nz in con.execute(f"SELECT item_id, name_zh FROM item_catalog WHERE item_id IN ({ph})", ids):
                names[iid] = nz
        except Exception:
            names = {}
        for r in rows:
            r["name"] = names.get(r["item_id"], "")
            r["store_name"] = store_zh(r["store_id"])
    con.close()
    return rows


def price_apply(store, action, value=0, both_dbs=True):
    paths = [DB] + ([STATIC] if both_dbs and os.path.exists(STATIC) else [])
    total = 0
    detail = []
    for p in paths:
        con = sqlite3.connect(p, timeout=15)
        cur = con.cursor()
        if action == "clear_gold":
            cur.execute("UPDATE shop_item SET gold=0 WHERE store_id=? AND silver>0 AND gold>0", (store,))
        elif action == "gold_to_silver":
            cur.execute("""UPDATE shop_item SET silver=gold, gold=0
                           WHERE store_id=? AND silver=0 AND gold>0
                             AND store_id NOT IN ('atm_01','atm_02')
                             AND item_id NOT LIKE 'wallet%'""", (store,))
        elif action == "mul":
            cur.execute("UPDATE shop_item SET silver=CAST(ROUND(silver*?) AS INTEGER) WHERE store_id=? AND silver>0",
                        (float(value or 1), store))
        elif action == "stock":
            cur.execute("UPDATE shop_item SET stock=? WHERE store_id=?", (int(value or 0), store))
        elif action == "row":
            iid, silver, gold, stock = value
            cur.execute("""UPDATE shop_item SET silver=?, gold=?, stock=?
                           WHERE store_id=? AND item_id=?""",
                        (int(silver), int(gold), int(stock), store, iid))
        n = cur.rowcount
        con.commit()
        con.close()
        total += n
        detail.append(f"{os.path.basename(p)}:{n}")
    return {"rows": total, "detail": ", ".join(detail)}


# ------------------------------ 参数 ------------------------------

def tunables():
    con = db()
    cur = dict((r["key"], r["value"]) for r in con.execute("SELECT key,value FROM tunables"))
    con.close()
    out = []
    for k, (label, desc, lo, hi) in TUNABLES.items():
        out.append({"key": k, "label": label, "desc": desc, "min": lo, "max": hi,
                    "value": cur.get(k), "in_db": k in cur})
    known = set(TUNABLES)
    for k, v in sorted(cur.items()):
        if k not in known:
            out.append({"key": k, "label": "", "desc": "库内其它参数", "min": None, "max": None,
                        "value": v, "in_db": True})
    return out


def tunable_set(key, value):
    con = sqlite3.connect(DB, timeout=10)
    con.execute("""INSERT INTO tunables(key,value,updated_ts) VALUES(?,?,strftime('%s','now'))
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts""",
                (key, str(value)))
    con.commit()
    con.close()
    return {"key": key, "value": str(value)}


# ------------------------------ 鱼口 ------------------------------

def fish_gaps(loc):
    """返回该水域 wiki 名单里有、loc_fish 里没有的鱼"""
    con = db()
    have = {r[0] for r in con.execute("SELECT fish_key FROM loc_fish WHERE loc=?", (loc,))}
    wiki = [r[0] for r in con.execute("SELECT fish_key FROM loc_wiki_fish WHERE loc=?", (loc,))]
    con.close()
    return [f for f in wiki if f not in have]


def fish_fill(dry=True, locs=None):
    """按 loc_wiki_fish 补齐 loc_fish，并用其它水域同一鱼种的饵料反推 loc_bait_fish"""
    con = sqlite3.connect(DB, timeout=20)
    cur = con.cursor()
    rarity = {r[0]: r[1] for r in cur.execute("SELECT fish_key, rarity FROM species_rarity")}
    targets = locs or [r[0] for r in cur.execute("SELECT loc FROM map_calib")]
    added_fish = added_bait = 0
    detail = []
    for loc in targets:
        gaps = fish_gaps(loc)
        if not gaps:
            continue
        map_baits = [r[0] for r in cur.execute("SELECT bait FROM loc_bait WHERE loc=?", (loc,))]
        n_f = n_b = 0
        for fk in gaps:
            r = rarity.get(fk, 1)
            w = 15 if r <= 0 else 10 if r == 1 else 6 if r == 2 else 3
            if not dry:
                cur.execute("INSERT OR REPLACE INTO loc_fish(loc,fish_key,name_zh,catches) VALUES(?,?,COALESCE((SELECT name_zh FROM loc_fish WHERE fish_key=? LIMIT 1),?),?)",
                            (loc, fk, fk, fk, w))
            n_f += 1
            # 用别的图里该鱼用过的饵，且本图也有这个饵
            cands = [x[0] for x in cur.execute(
                "SELECT DISTINCT bait FROM loc_bait_fish WHERE fish_key=? AND loc<>? LIMIT 20", (fk, loc))]
            use = [b for b in cands if b in map_baits][:3]
            for b in use:
                if not dry:
                    cur.execute("""INSERT OR IGNORE INTO loc_bait_fish(loc,bait,fish_key,catches,trophy,super)
                                   VALUES(?,?,?,1,0,0)""", (loc, b, fk))
                n_b += 1
        added_fish += n_f
        added_bait += n_b
        detail.append({"loc": loc, "fish": n_f, "bait": n_b})
    if not dry:
        con.commit()
    con.close()
    return {"dry": dry, "fish": added_fish, "bait": added_bait, "detail": detail}


def fish_coverage():
    con = db()
    locs = [r[0] for r in con.execute("SELECT loc FROM map_calib")]
    out = []
    for loc in locs:
        f = con.execute("SELECT COUNT(DISTINCT fish_key), COALESCE(SUM(catches),0) FROM loc_fish WHERE loc=?", (loc,)).fetchone()
        s = con.execute("SELECT COUNT(*), COUNT(DISTINCT spot) FROM spot_fish WHERE loc=?", (loc,)).fetchone()
        b = con.execute("SELECT COUNT(*) FROM loc_bait WHERE loc=?", (loc,)).fetchone()[0]
        bf = con.execute("SELECT COUNT(*) FROM loc_bait_fish WHERE loc=?", (loc,)).fetchone()[0]
        bs = con.execute("SELECT COALESCE(bite_probability,1), COALESCE(bite_delay_fixed,0), COALESCE(bite_delay_scale,1) FROM fish_bite_setting WHERE loc=?", (loc,)).fetchone()
        have = {r[0] for r in con.execute("SELECT fish_key FROM loc_fish WHERE loc=?", (loc,))}
        wiki = {r[0] for r in con.execute("SELECT fish_key FROM loc_wiki_fish WHERE loc=?", (loc,))}
        gap = len(wiki - have) if wiki else 0
        score = 0
        if f[0] >= 30: score += 2
        elif f[0] >= 20: score += 1
        if f[1] >= 300: score += 2
        elif f[1] >= 100: score += 1
        if bf >= 200: score += 1
        out.append({"loc": loc, "fish": f[0], "samples": f[1], "spots": s[1] or 0,
                    "spot_fish": s[0] or 0, "baits": b, "bait_fish": bf, "gap": gap,
                    "bite": {"p": bs[0], "fixed": bs[1], "scale": bs[2]} if bs else None,
                    "thin": score <= 2})
    con.close()
    out.sort(key=lambda x: (x["fish"], x["samples"]))
    return out


# ------------------------------ HTTP ------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>RF4 扩展管理</title>
<style>
:root{--bg:#0b1220;--panel:#121c2b;--line:#1e2c40;--text:#dbe6f3;--muted:#7f93aa;--acc:#39b3ff;--ok:#4ad991;--warn:#ffb454;--bad:#ff6b81}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:13px/1.6 "Segoe UI","Microsoft YaHei",system-ui}
header{display:flex;gap:18px;align-items:center;padding:12px 18px;border-bottom:1px solid var(--line);background:#0a1421}
h1{font-size:15px;margin:0;font-weight:600}
nav{display:flex;gap:6px}
nav button{background:#16233a;color:var(--text);border:1px solid var(--line);padding:6px 14px;border-radius:8px;cursor:pointer}
nav button.on{background:var(--acc);color:#04121f;border-color:var(--acc);font-weight:600}
main{padding:16px 18px;max-width:1400px}
section{display:none}section.on{display:block}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:500;position:sticky;top:0;background:var(--panel)}
tr:hover td{background:#16233a}
input,select{background:#0d1726;border:1px solid var(--line);color:var(--text);padding:5px 8px;border-radius:6px;font:inherit}
input[type=number]{width:90px}
button.act{background:#1c2c44;border:1px solid var(--line);color:var(--text);padding:5px 10px;border-radius:6px;cursor:pointer}
button.act:hover{border-color:var(--acc)}
button.pri{background:var(--acc);color:#04121f;border:none;font-weight:600}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:12px}
.scroll{max-height:62vh;overflow:auto;border:1px solid var(--line);border-radius:10px}
.muted{color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.tag{display:inline-block;padding:1px 6px;border-radius:5px;font-size:11px;background:#1c2c44}
#toast{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:8px;z-index:9}
#toast div{background:#16233a;border:1px solid var(--line);border-left:3px solid var(--acc);padding:8px 12px;border-radius:8px;max-width:420px}
code{background:#0d1726;padding:1px 5px;border-radius:4px}
</style></head><body>
<header><h1>RF4 扩展管理</h1><nav>
<button data-t="shop" class="on">商店批量</button>
<button data-t="tun">参数</button>
<button data-t="fish">鱼口监测</button>
</nav><span class="muted" id="hint">改完商店/参数需重启 gameserver 生效</span></header>
<main>

<section id="shop" class="on">
  <div class="bar">
    <label class="muted"><input type="checkbox" id="globalSearch" onchange="onGlobalToggle()"> 全局搜索</label>
    <select id="store"></select>
    <input id="q" placeholder="搜索商品（item_id 或中文名）" style="width:240px">
    <button class="act" onclick="loadItems()">查询</button>
    <span class="muted" id="shopInfo"></span>
  </div>
  <div class="bar" id="bulkBar">
    <button class="act" onclick="bulk('clear_gold')">清掉本店金币价（改银币）</button>
    <button class="act" onclick="bulk('gold_to_silver')">纯金币商品转银币</button>
    <span class="muted">银币价 ×</span><input type="number" id="mul" value="1" step="0.05" style="width:80px">
    <button class="act" onclick="bulk('mul')">应用</button>
    <span class="muted">库存设为</span><input type="number" id="stk" value="999" style="width:90px">
    <button class="act" onclick="bulk('stock')">应用</button>
  </div>
  <div class="scroll"><table id="itemTable"><thead><tr id="itemHead"><th>商品</th><th>商店</th><th>银币</th><th>金币</th><th>库存</th><th></th></tr></thead><tbody></tbody></table></div>
</section>

<section id="tun">
  <div class="card">参数修改后需**重启 gameserver** 才生效。带“库中无”的表示当前用默认值，填上即可覆盖。</div>
  <div class="scroll"><table id="tunTable"><thead><tr><th>参数</th><th>当前值</th><th>说明</th><th></th></tr></thead><tbody></tbody></table></div>
</section>

<section id="fish">
  <div class="bar"><button class="act" onclick="loadFish()">刷新</button>
    <button class="act" onclick="fishFill(true)">预览补齐缺口</button>
    <button class="act pri" onclick="fishFill(false)">执行补齐（写库）</button>
    <span class="muted">依据 <code>loc_wiki_fish</code> 名单补 <code>loc_fish</code>，并用其它水域同鱼种用过的饵反推 <code>loc_bait_fish</code>；执行后需重启 gameserver</span></div>
  <div class="scroll"><table id="fishTable"><thead><tr><th>水域</th><th>鱼种</th><th>样本</th><th>缺口</th><th>点位数</th><th>点×鱼</th><th>饵</th><th>饵×鱼</th><th>咬钩</th><th></th></tr></thead><tbody></tbody></table></div>
</section>
</main>
<div id="toast"></div>
<script>
const $=s=>document.querySelector(s);
function toast(m,bad){const d=document.createElement('div');d.textContent=m;if(bad)d.style.borderLeftColor='var(--bad)';$('#toast').appendChild(d);setTimeout(()=>d.remove(),3500);}
async function api(p,opt){const r=await fetch(p,opt);const t=await r.text();let d={};try{d=JSON.parse(t)}catch(e){d={error:t.slice(0,200)}}if(!r.ok||d.error)throw new Error(d.error||('HTTP '+r.status));return d;}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x===b));
  document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id===b.dataset.t));
  if(b.dataset.t==='shop'&&!$('#store').options.length)initShop();
  if(b.dataset.t==='tun')loadTun();
  if(b.dataset.t==='fish')loadFish();
});
async function initShop(){
  const d=await api('/api/stores');
  $('#store').innerHTML=d.stores.map(s=>`<option value="${s.store_id}">${s.name}${s.name!==s.store_id?' ('+s.store_id+')':''} · ${s.n} 件${s.both_g?` · 双价${s.both_g}`:''}${s.gold_only?` · 金币${s.gold_only}`:''}</option>`).join('');
  loadItems();
}
function onGlobalToggle(){
  const g=$('#globalSearch').checked;
  $('#store').disabled=g;
  $('#bulkBar').style.display=g?'none':'';
  // 切换表头（全局模式有商店列）
  const head=$('#itemHead');
  head.innerHTML=g
    ? '<th>商品</th><th>商店</th><th>银币</th><th>金币</th><th>库存</th><th></th>'
    : '<th>商品</th><th>银币</th><th>金币</th><th>库存</th><th></th>';
}
async function loadItems(){
  const global=$('#globalSearch').checked;
  const st=global?'':$('#store').value;
  const q=$('#q').value;
  const d=await api(`/api/items?store=${encodeURIComponent(st)}&q=${encodeURIComponent(q)}`);
  $('#shopInfo').textContent=`${d.rows.length} 行`;
  const storeCol=r=>`<td class="muted">${r.store_name||''}</td>`;
  $('#itemTable tbody').innerHTML=d.rows.map(r=>`<tr data-id="${r.item_id}" data-store="${r.store_id}">
    <td><b>${r.name||''}</b>${r.name?'<br><span class="muted">'+r.item_id+'</span>':r.item_id}</td>
    ${global?storeCol(r):''}
    <td><input type="number" step="0.01" value="${(r.silver/100).toFixed(2)}" data-f="silver" style="width:110px"></td>
    <td><input type="number" step="0.01" value="${(r.gold/100).toFixed(2)}" data-f="gold" style="width:100px"></td>
    <td><input type="number" value="${r.stock}" data-f="stock" style="width:100px"></td>
    <td><button class="act" onclick="saveRow(this)">保存</button></td></tr>`).join('');
}
async function saveRow(btn){
  const tr=btn.closest('tr'),id=tr.dataset.id,st=tr.dataset.store||$('#store').value;
  const g=n=>Number(tr.querySelector(`[data-f="${n}"]`).value);
  // silver/gold 字段在库里都是"分"，界面显示"币"（÷100），保存时 ×100 写回
  const silverVal = Math.round(g('silver') * 100);
  const goldVal = Math.round(g('gold') * 100);
  await api('/api/items/row',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({store:st,item_id:id,silver:silverVal,gold:goldVal,stock:g('stock')})});
  toast('已保存 '+id);
}
async function bulk(action){
  const st=$('#store').value;
  const body={store:st,action};
  if(action==='mul')body.value=Number($('#mul').value);
  if(action==='stock')body.value=Number($('#stk').value);
  const d=await api('/api/items/bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  toast(`更新 ${d.rows} 行（${d.detail}）`);
  loadItems();
}
async function loadTun(){
  const d=await api('/api/tunables');
  $('#tunTable tbody').innerHTML=d.items.map(t=>`<tr>
    <td><b>${t.label||t.key}</b><br><span class="muted">${t.key}</span></td>
    <td><input type="number" step="any" value="${t.value??''}" placeholder="默认" style="width:110px" data-k="${t.key}">
      ${t.in_db?'':'<span class="tag">库中无</span>'}</td>
    <td class="muted">${t.desc}</td>
    <td><button class="act" onclick="saveTun('${t.key}',this)">保存</button></td></tr>`).join('');
}
async function saveTun(k,btn){
  const v=btn.closest('tr').querySelector('input').value;
  await api('/api/tunables',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:k,value:v})});
  toast('已保存 '+k+' = '+v+'（重启生效）');
}
async function loadFish(){
  const d=await api('/api/fish');
  $('#fishTable tbody').innerHTML=d.rows.map(r=>`<tr>
    <td>${r.loc}</td><td class="${r.fish<20?'bad':''}">${r.fish}</td><td class="${r.samples<100?'bad':''}">${r.samples}</td>
    <td class="${r.gap>0?'warn':'muted'}">${r.gap||0}</td>
    <td>${r.spots}</td><td>${r.spot_fish}</td><td>${r.baits}</td><td>${r.bait_fish}</td>
    <td class="muted">${r.bite?`p=${r.bite.p} 固定${r.bite.fixed}s ×${r.bite.scale}`:'—'}</td>
    <td>${r.thin?'<span class="bad">偏薄</span>':'<span class="ok">正常</span>'}</td></tr>`).join('');
}
async function fishFill(dry){
  if(!dry && !confirm('确定写入数据库？建议先"预览"。'))return;
  const d=await api('/api/fish/fill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dry:dry})});
  const rows=d.detail.map(x=>`${x.loc}: 鱼 ${x.fish} / 饵 ${x.bait}`).join('\n');
  toast((dry?'预览':'已写入')+`：补 ${d.fish} 种鱼、${d.bait} 条饵料映射`);
  if(rows)alert((dry?'【预览】将补齐：\n':'【已写入】\n')+rows+(dry?'\n\n确认无误后点"执行补齐"。':'\n\n重启 gameserver 生效。'));
  if(!dry)loadFish();
}
initShop();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "RF4Tool/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=str).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if u.path == "/api/stores":
                return self._send(200, {"stores": stores()})
            if u.path == "/api/items":
                return self._send(200, {"rows": items(qs.get("store", [""])[0], qs.get("q", [""])[0])})
            if u.path == "/api/tunables":
                return self._send(200, {"items": tunables()})
            if u.path == "/api/fish":
                return self._send(200, {"rows": fish_coverage()})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        b = self._body()
        try:
            if u.path == "/api/items/bulk":
                return self._send(200, price_apply(b.get("store"), b.get("action"), b.get("value", 0)))
            if u.path == "/api/items/row":
                v = (b.get("item_id"), b.get("silver", 0), b.get("gold", 0), b.get("stock", 0))
                return self._send(200, price_apply(b.get("store"), "row", v))
            if u.path == "/api/tunables":
                return self._send(200, tunable_set(b["key"], b.get("value")))
            if u.path == "/api/fish/fill":
                return self._send(200, fish_fill(dry=bool(b.get("dry", True)), locs=b.get("locs")))
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    print(f"RF4 扩展管理工具  http://{HOST}:{PORT}")
    print(f"根目录: {ROOT}")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
