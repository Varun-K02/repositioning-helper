import os
from config import WEB_ROOT

def write_viewer_html():
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Interactive Hole Detector</title>
  <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
  <style>
    body { font-family: Arial, sans-serif; background:#f7f7fb; margin:10px; }
    #controls { padding:10px; background:white; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.06); width:100%; max-width:1200px; margin-bottom:12px; }
    #progressWrapper { display:flex; align-items:center; gap:12px; }
    #progressBar { width: 400px; height:14px; background:#e9ecef; border-radius:8px; overflow:hidden; }
    #progressFill { height:100%; width:0%; background:#28a745; transition: width 0.2s ease; }
    #statusText { min-width:220px; }
    #plotContainer { width: 100%; max-width:1400px; }
    button { padding:8px 12px; border-radius:6px; border:none; cursor:pointer; }
    button.primary { background:#007bff; color:white; }
    button.green { background:#28a745; color:white; }
    button.red { background:#dc3545; color:white; }
    button:hover { opacity:0.9; transform:translateY(-1px); }
    input[type=file] { padding:6px; }
    #selectionPanel {
      position: relative;
      margin-top: 15px;
      background: white;
      padding: 20px;
      border-radius: 10px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
      width: 100%;
      max-width: 1200px;
      margin-left: auto;
      margin-right: auto;
      display: none;
    }
    #selectionPanel h3 { margin: 0 0 15px 0; }
    #selectionCount { font-weight: bold; color: #0066cc; margin-bottom: 10px; }
    #selectionList {
      padding: 10px;
      background: #f5f5f5;
      border-radius: 5px;
      margin-bottom: 15px;
      min-height: 40px;
      max-height: 150px;
      overflow-y: auto;
      font-size: 13px;
    }
    #exportStatus {
      margin-top: 15px;
      padding: 10px;
      border-radius: 5px;
      display: none;
    }
  </style>
</head>
<body>
  <div id="controls">
    <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
      <input id="stepFile" type="file" accept=".stp,.step"/>
      <button id="btnLoad" class="primary">Load STEP & Detect Holes</button>
      <button id="btnDelete" class="red">Clear All</button>
      <div id="progressWrapper" style="margin-left:20px;">
        <div id="progressBar"><div id="progressFill"></div></div>
        <div id="statusText">Idle</div>
      </div>
      <div style="margin-left:auto; font-size:13px; color:#666;">Status: <span id="globalStatus">Ready</span></div>
    </div>
  </div>

  <div id="plotContainer">
    <div id="plot" style="width:100%;height:760px;"></div>
  </div>

  <div id="selectionPanel">
    <h3>Hole Selection</h3>
    <div id="selectionCount">Selected: 0</div>
    <div id="selectionList">Click on hole markers to select</div>
    <button onclick="exportHoles()" style="width:100%;padding:12px;background:#28a745;color:white;border:none;border-radius:5px;cursor:pointer;font-weight:bold;margin-bottom:8px">Export Selected</button>
    <button onclick="selectAll()" style="width:100%;padding:8px;background:#007bff;color:white;border:none;border-radius:5px;cursor:pointer;margin-bottom:8px">Select All</button>
    <button onclick="clearSelection()" style="width:100%;padding:8px;background:#dc3545;color:white;border:none;border-radius:5px;cursor:pointer">Clear Selection</button>
    <div id="exportStatus"></div>
  </div>

<script>
let currentUID = null;
let meshLoaded = false;
let holesLoaded = false;
let allHoles = [];
let selectedHoles = new Set();

function createInitialPlot() {
  const data = [];
  const layout = {
    title: 'CAD Viewer with Hole Detection',
    scene: {
      xaxis: {title:'X'}, yaxis: {title:'Y'}, zaxis: {title:'Z'}, aspectmode:'data',
    },
    height:760,
    autosize:true
  };
  Plotly.newPlot('plot', data, layout);
}

createInitialPlot();

function setProgress(percent, status) {
  document.getElementById('progressFill').style.width = percent + '%';
  document.getElementById('statusText').textContent = status + ' (' + percent + '%)';
}

function pollProgress(uid) {
  return fetch('/progress?uid=' + uid)
    .then(r => r.json())
    .catch(e => { return {percent:100, status:'Error polling'}; });
}

async function waitForProcessing(uid) {
  currentUID = uid;
  setProgress(1, 'Queued');
  
  while (true) {
    const p = await pollProgress(uid);
    setProgress(p.percent || 0, p.status || 'Working');
    if ((p.percent || 0) >= 100) break;
    await new Promise(r => setTimeout(r, 500));
  }

  // Load mesh and holes
  try {
    await loadMesh(uid);
    await loadHoles(uid);
    document.getElementById('globalStatus').textContent = 'Ready - Click holes to select';
    document.getElementById('selectionPanel').style.display = 'block';
  } catch (e) {
    alert('Failed to load data: ' + e);
  }
}

async function loadMesh(uid) {
  const meshUrl = `/mesh/mesh_${uid}.json`;
  try {
    const r = await fetch(meshUrl);
    if (!r.ok) {
      console.warn('Mesh file not available');
      return;
    }
    const mesh = await r.json();
    
    if (!mesh || !mesh.vertices || mesh.vertices.length === 0) {
      console.warn('Empty mesh data');
      return;
    }

    const xs = mesh.vertices.map(v => v[0]);
    const ys = mesh.vertices.map(v => v[1]);
    const zs = mesh.vertices.map(v => v[2]);
    const i = mesh.faces.map(f => f[0]);
    const j = mesh.faces.map(f => f[1]);
    const k = mesh.faces.map(f => f[2]);

    const trace = {
      type:'mesh3d',
      x: xs, y: ys, z: zs,
      i: i, j: j, k: k,
      opacity: 0.5,
      color: 'lightgray',
      name: 'CAD Model',
      showscale: false,
      flatshading: true,
      lighting: {
        ambient: 0.8,
        diffuse: 0.8,
        specular: 0.2,
        roughness: 0.5,
        fresnel: 0.2
      },
      hoverinfo: 'skip'
    };

    Plotly.addTraces('plot', trace);
    meshLoaded = true;
    autoFitCamera(xs, ys, zs);
  } catch (e) {
    console.error('Failed to load mesh:', e);
  }
}

async function loadHoles(uid) {
  const holesUrl = `/holes/holes_${uid}.json`;
  try {
    const r = await fetch(holesUrl);
    if (!r.ok) {
      throw new Error('Holes file not available');
    }
    allHoles = await r.json();
    
    if (!allHoles || allHoles.length === 0) {
      alert('No holes detected in this model');
      return;
    }

    displayHoles();
    holesLoaded = true;
  } catch (e) {
    console.error('Failed to load holes:', e);
    alert('Failed to load hole data');
  }
}

function displayHoles() {
  const x = allHoles.map(h => h.center[0]);
  const y = allHoles.map(h => h.center[1]);
  const z = allHoles.map(h => h.center[2]);
  const ids = allHoles.map(h => h.id);
  
  const colors = allHoles.map(h => {
    if (selectedHoles.has(h.id)) return 'rgb(0,255,0)';
    return h.num_circles >= 3 ? 'rgb(30,144,255)' : 
           h.num_circles == 2 ? 'rgb(255,69,0)' : 'rgb(255,215,0)';
  });

  const hover = allHoles.map(h => 
    `<b>ID: ${h.id}</b><br>Score: ${h.score.toFixed(0)}<br>Circles: ${h.num_circles}<br>` +
    `X: ${h.center[0].toFixed(0)} Y: ${h.center[1].toFixed(0)} Z: ${h.center[2].toFixed(0)}<br>` +
    `Radius: ${h.radius.toFixed(2)}mm`
  );

  const trace = {
    type: 'scatter3d',
    x: x, y: y, z: z,
    mode: 'markers+text',
    text: ids.map(i => String(i)),
    textposition: 'top center',
    textfont: {size: 10, color: 'white'},
    marker: {
      size: 8,
      color: colors,
      line: {color: 'white', width: 1}
    },
    hovertext: hover,
    hoverinfo: 'text',
    name: 'Holes',
    customdata: ids
  };

  Plotly.addTraces('plot', trace);
  
  // Add click handler
  const plot = document.getElementById('plot');
  plot.on('plotly_click', handleHoleClick);
}

function handleHoleClick(data) {
  try {
    if (!data || !data.points || data.points.length === 0) return;
    const pt = data.points[0];
    if (!pt || !pt.data || pt.data.name !== 'Holes') return;
    
    const holeId = pt.customdata;
    toggleHole(holeId);
  } catch (e) {
    console.error('Click handler error:', e);
  }
}

async function toggleHole(holeId) {
  try {
    const r = await fetch(`/api/toggle?uid=${currentUID}&id=${holeId}`);
    const resp = await r.json();
    selectedHoles = new Set(resp.selected);
    updateHoleColors();
    updateSelectionPanel();
  } catch (e) {
    console.error('Toggle error:', e);
  }
}

function updateHoleColors() {
  const colors = allHoles.map(h => {
    if (selectedHoles.has(h.id)) return 'rgb(0,255,0)';
    return h.num_circles >= 3 ? 'rgb(30,144,255)' : 
           h.num_circles == 2 ? 'rgb(255,69,0)' : 'rgb(255,215,0)';
  });

  const holeTraceIndex = meshLoaded ? 1 : 0;
  Plotly.restyle('plot', {'marker.color': [colors]}, [holeTraceIndex]);
}

function updateSelectionPanel() {
  document.getElementById('selectionCount').textContent = `Selected: ${selectedHoles.size}`;
  const sortedIds = Array.from(selectedHoles).sort((a,b) => a - b);
  document.getElementById('selectionList').textContent = sortedIds.length > 0 ? 
    sortedIds.join(', ') : 'Click on hole markers to select';
}

async function selectAll() {
  const promises = allHoles.map(h => {
    if (!selectedHoles.has(h.id)) {
      return fetch(`/api/toggle?uid=${currentUID}&id=${h.id}`);
    }
  }).filter(Boolean);
  
  await Promise.all(promises);
  
  // Simpler approach: just select all locally
  selectedHoles = new Set(allHoles.map(h => h.id));
  
  // Update server state for each
  for (const h of allHoles) {
    await fetch(`/api/toggle?uid=${currentUID}&id=${h.id}`);
  }
  
  updateHoleColors();
  updateSelectionPanel();
}

async function clearSelection() {
  const promises = Array.from(selectedHoles).map(id =>
    fetch(`/api/toggle?uid=${currentUID}&id=${id}`)
  );
  await Promise.all(promises);
  selectedHoles.clear();
  updateHoleColors();
  updateSelectionPanel();
}

async function exportHoles() {
  if (selectedHoles.size === 0) {
    showExportStatus('Please select at least one hole', 'error');
    return;
  }

  try {
    const r = await fetch(`/api/export?uid=${currentUID}`);
    const resp = await r.json();
    showExportStatus(`Exported ${resp.count} holes to ${resp.file}`, 'success');
  } catch (e) {
    console.error('Export error:', e);
    showExportStatus('Export failed', 'error');
  }
}

function showExportStatus(msg, type) {
  const status = document.getElementById('exportStatus');
  status.style.display = 'block';
  status.style.background = type === 'success' ? '#d4edda' : '#f8d7da';
  status.style.color = type === 'success' ? '#155724' : '#721c24';
  status.textContent = msg;
  setTimeout(() => status.style.display = 'none', 3000);
}

function autoFitCamera(xs, ys, zs) {
  let minX = Infinity, maxX = -Infinity;
  let minY = Infinity, maxY = -Infinity;
  let minZ = Infinity, maxZ = -Infinity;
  
  for (let i = 0; i < xs.length; i++) {
    if (xs[i] < minX) minX = xs[i];
    if (xs[i] > maxX) maxX = xs[i];
    if (ys[i] < minY) minY = ys[i];
    if (ys[i] > maxY) maxY = ys[i];
    if (zs[i] < minZ) minZ = zs[i];
    if (zs[i] > maxZ) maxZ = zs[i];
  }
  
  const center = [(minX+maxX)/2, (minY+maxY)/2, (minZ+maxZ)/2];
  const rangeX = maxX-minX || 1;
  const rangeY = maxY-minY || 1;
  const rangeZ = maxZ-minZ || 1;
  const maxRange = Math.max(rangeX, rangeY, rangeZ);

  const eye = { x: center[0] + maxRange*1.5, y: center[1] + maxRange*1.5, z: center[2] + maxRange*1.0 };
  Plotly.relayout('plot', {
    'scene.camera.eye': eye,
    'scene.camera.center': {x:center[0], y:center[1], z:center[2]},
  });
}

document.getElementById('btnLoad').addEventListener('click', async () => {
  const fileInput = document.getElementById('stepFile');
  if (!fileInput.files || fileInput.files.length === 0) {
    alert('Please select a STEP file (.stp/.step)');
    return;
  }
  
  // Reset state
  meshLoaded = false;
  holesLoaded = false;
  allHoles = [];
  selectedHoles.clear();
  document.getElementById('selectionPanel').style.display = 'none';
  Plotly.purge('plot');
  createInitialPlot();
  
  const fd = new FormData();
  fd.append('stepfile', fileInput.files[0]);

  setProgress(0, 'Uploading');
  document.getElementById('globalStatus').textContent = 'Uploading...';

  const resp = await fetch('/upload_step', { method: 'POST', body: fd });
  if (!resp.ok) {
    const txt = await resp.text();
    alert('Upload failed: ' + txt);
    return;
  }
  const body = await resp.json();
  document.getElementById('globalStatus').textContent = 'Processing...';
  await waitForProcessing(body.uid);
});

document.getElementById('btnDelete').addEventListener('click', async () => {
  if (!currentUID) {
    alert('No data loaded');
    return;
  }
  
  await fetch('/delete?uid=' + currentUID);
  
  currentUID = null;
  meshLoaded = false;
  holesLoaded = false;
  allHoles = [];
  selectedHoles.clear();
  document.getElementById('selectionPanel').style.display = 'none';
  Plotly.purge('plot');
  createInitialPlot();
  setProgress(0, 'Deleted');
  document.getElementById('globalStatus').textContent = 'Ready';
});

</script>
</body>
</html>
"""
    os.makedirs(WEB_ROOT, exist_ok=True)
    path = os.path.join(WEB_ROOT, "viewer.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)