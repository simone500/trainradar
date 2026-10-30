import requests, urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from flask import Flask, jsonify, request, Response

VT = "http://www.viaggiatreno.it/infomobilitamobile/resteasy/viaggiatreno/"
app = Flask(__name__)

def vt(path):
    r = requests.get(VT + path, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r

@lru_cache(maxsize=5000)
def coords(sid):
    try:
        reg = vt(f"regione/{sid}").text.strip()
        d = vt(f"dettaglioStazione/{sid}/{reg}").json()
        return d["lat"], d["lon"]
    except Exception:
        return None

@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")

@app.route("/api/stazioni")
def stazioni():
    q = request.args.get("q", "")
    out = []
    for line in vt("autocompletaStazione/" + urllib.parse.quote(q)).text.splitlines():
        if "|" in line:
            n, i = line.split("|", 1)
            out.append({"nome": n, "id": i})
    return jsonify(out)

@app.route("/api/partenze/<sid>")
def partenze(sid):
    d = datetime.now().strftime("%a %b %d %Y %H:%M:%S GMT+0100")
    data = vt(f"partenze/{sid}/{urllib.parse.quote(d)}").json()
    return jsonify([{
        "numero": t.get("numeroTreno"), "categoria": t.get("categoria"),
        "destinazione": t.get("destinazione"), "orario": t.get("compOrarioPartenza"),
        "ritardo": t.get("ritardo"), "binario": t.get("binarioEffettivoPartenzaDescrizione") or t.get("binarioProgrammatoPartenzaDescrizione"),
        "origine": t.get("codOrigine"), "epoch": t.get("dataPartenzaTreno")} for t in data])

@app.route("/api/treno/<numero>")
def treno(numero):
    lines = [l for l in vt("cercaNumeroTrenoTrenoAutocomplete/" + numero).text.splitlines() if "|" in l]
    if not lines:
        return jsonify({"errore": "Treno non trovato"}), 404
    _, key = lines[0].split("|", 1)
    _, origine, epoch = key.split("-")
    return andamento(origine, numero, epoch)

@app.route("/api/andamento/<origine>/<numero>/<epoch>")
def andamento(origine, numero, epoch):
    d = vt(f"andamentoTreno/{origine}/{numero}/{epoch}").json()
    fer = d.get("fermate", [])
    with ThreadPoolExecutor(8) as ex:
        cs = list(ex.map(coords, [f["id"] for f in fer]))
    return jsonify({
        "numero": numero, "origine": d.get("origine"), "destinazione": d.get("destinazione"),
        "ritardo": d.get("ritardo"), "ultimo": d.get("stazioneUltimoRilevamento"),
        "ultimoOra": d.get("oraUltimoRilevamento"), "stato": d.get("subTitle"),
        "fermate": [{"nome": f["stazione"], "prog": f.get("programmata"), "eff": f.get("effettiva"),
                     "ritardo": f.get("ritardo"), "lat": c[0] if c else None, "lon": c[1] if c else None}
                    for f, c in zip(fer, cs)]})


PAGE = r"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trainradar</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
body{margin:0;font-family:system-ui,sans-serif;display:flex;flex-direction:column;height:100vh}
header{padding:10px;background:#c8102e;color:#fff;display:flex;gap:8px;flex-wrap:wrap}
input,button{padding:8px;font-size:15px;border-radius:6px;border:0}
button{background:#fff;cursor:pointer}
main{flex:1;display:flex;min-height:0}
#side{width:340px;overflow:auto;padding:10px}
#map{flex:1}
.row{padding:6px 0;border-bottom:1px solid #ddd;cursor:pointer}
.late{color:#c00}.ok{color:#080}
@media(max-width:700px){main{flex-direction:column}#side{width:auto;height:40%}}
</style></head><body>
<header><b style="font-size:18px;align-self:center">🚆 Trainradar</b>
<input id="num" placeholder="N° treno" size="8"><button onclick="cercaTreno()">Cerca treno</button>
<input id="st" placeholder="Stazione" list="sl" oninput="suggerisci()"><datalist id="sl"></datalist>
<button onclick="cercaStazione()">Partenze</button>
</header>
<main><div id="side">Cerca un treno o una stazione.</div><div id="map"></div></main>
<script>
const map=L.map('map').setView([42.5,12.5],6);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap'}).addTo(map);
let layer=L.layerGroup().addTo(map),stazioni={};
const side=document.getElementById('side');
const cls=r=>r>0?'late':'ok', fmt=r=>r>0?`+${r} min`:'in orario';
const hm=t=>t?new Date(t).toLocaleTimeString('it',{hour:'2-digit',minute:'2-digit'}):'–';
async function j(u){const r=await fetch(u);if(!r.ok)throw 0;return r.json()}
async function suggerisci(){
  const q=document.getElementById('st').value;if(q.length<2)return;
  const l=await j('/api/stazioni?q='+encodeURIComponent(q));stazioni={};
  document.getElementById('sl').innerHTML=l.map(s=>{stazioni[s.nome]=s.id;return`<option value="${s.nome}">`}).join('');
}
async function cercaStazione(){
  const id=stazioni[document.getElementById('st').value];if(!id)return side.textContent='Scegli una stazione dai suggerimenti.';
  const l=await j('/api/partenze/'+id);
  side.innerHTML=l.map(t=>`<div class="row" onclick="apri('${t.origine}',${t.numero},${t.epoch})"><b>${t.orario}</b> ${t.categoria} ${t.numero} → ${t.destinazione}<br><span class="${cls(t.ritardo)}">${fmt(t.ritardo)}</span> · binario ${t.binario||'?'}</div>`).join('')||'Nessuna partenza.';
}
function cercaTreno(){mostra('/api/treno/'+document.getElementById('num').value)}
function apri(o,n,e){mostra(`/api/andamento/${o}/${n}/${e}`)}
async function mostra(u){
  try{var d=await j(u)}catch{return side.textContent='Treno non trovato.'}
  layer.clearLayers();
  const pts=d.fermate.filter(f=>f.lat).map(f=>[f.lat,f.lon]);
  if(pts.length){L.polyline(pts,{color:'#c8102e'}).addTo(layer);map.fitBounds(pts)}
  d.fermate.forEach(f=>{if(f.lat)L.circleMarker([f.lat,f.lon],{radius:5,color:f.nome==d.ultimo?'#00f':'#c8102e'}).bindPopup(f.nome).addTo(layer)});
  side.innerHTML=`<h3>Treno ${d.numero}</h3>${d.origine} → ${d.destinazione}<br><span class="${cls(d.ritardo)}">${fmt(d.ritardo)}</span><br>Ultimo rilevamento: ${d.ultimo||'–'} ${d.ultimoOra?hm(d.ultimoOra):''}<hr>`+
   d.fermate.map(f=>`<div class="row"><b>${f.nome}</b><br>${hm(f.prog)} → ${hm(f.eff)} <span class="${cls(f.ritardo)}">${fmt(f.ritardo)}</span></div>`).join('');
}
</script></body></html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
