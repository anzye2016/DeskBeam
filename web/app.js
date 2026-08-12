
var _gBtns=[],_gHoldUp=[];
var _BASE=new URL('.',location.href).pathname.replace(/\/+$/,'');
var _isTouch=('ontouchstart' in window);
var _isLan=true,_mqx=0,_mqy=0,_mqT=null;
function _mqAcc(dx,dy){
  _mqx+=dx;_mqy+=dy;
  if(!_mqT){_mqT=setTimeout(function(){_mqT=null;if(_mqx||_mqy){sendCmd({type:'mouse_move',dx:_mqx,dy:_mqy});_mqx=0;_mqy=0;}},8);}
}
var ks=document.getElementById('ks');
function _mkBtn(spec){
  var b=document.createElement('button');b.className='k';b.textContent=spec.t;
  if(spec.m==='hold'){_bindHoldEl(b,spec.d,spec.u,spec.nc||false,spec.nr);}
  else if(spec.m==='rep'){_bindRepEl(b,spec.c);}
  else if(spec.fn){_tapEl(b,spec.fn);}
  else{_tapEl(b,function(){sendCmd({type:spec.c});});}
  return b;
}
var _MOUSE=[
  {t:'L',m:'hold',d:{type:'mouse_down'},u:{type:'mouse_up'},nr:true,nc:true},
  {t:'R',m:'hold',d:{type:'mouse_right_down'},u:{type:'mouse_right_up'},nr:true,nc:true},
  {t:'▲',m:'hold',d:{type:'scroll_up'},u:null,nc:true},
  {t:'▼',m:'hold',d:{type:'scroll_down'},u:null,nc:true},
  {t:'⌫',c:'backspace'},{t:'Enter',c:'enter'},
];
var _KEYS=[
  {t:'Ctrl+C',c:'ctrl_c'},{t:'Esc',c:'esc'},{t:'Ctrl+V',c:'ctrl_v'},
  {t:'Drag',fn:function(){_dm=!_dm;var b=this;b.style.color=_dm?'var(--green)':'';b.style.borderColor=_dm?'var(--green)':'';if(_dm)sendCmd({type:'mouse_down'});else sendCmd({type:'mouse_up'});}},
  {t:'M',c:'mouse_middle'},{t:'Ctrl+A',c:'ctrl_a'},{t:'Ctrl+Z',c:'ctrl_z'},
  {t:'Ctrl+X',c:'ctrl_x'},{t:'Ctrl+S',c:'ctrl_s'},{t:'Tab',c:'tab'},{t:'Alt+Tab',c:'alt_tab'},
  {t:'Win',c:'win'},{t:'F5',c:'f5'},{t:'Ctrl+F5',c:'ctrl_f5'},{t:'Ctrl+J',c:'ctrl_j'},
  {t:'Shift+Enter',c:'shift_enter'},{t:'F12',c:'f12'},
  {t:'UAC',fn:_espUac},{t:'JS',fn:_espJs},{t:'ESP ESC',fn:_espEsc},{t:'Lock',fn:_espLock},
  {t:'ESP Win',fn:_espWin},{t:'ESP L',fn:_espL},
  {t:'↑',m:'hold',d:{type:'key_down',key:'up'},u:{type:'key_up',key:'up'},nr:true,nc:true},
  {t:'↓',m:'hold',d:{type:'key_down',key:'down'},u:{type:'key_up',key:'down'},nr:true,nc:true},
  {t:'←',m:'hold',d:{type:'key_down',key:'left'},u:{type:'key_up',key:'left'},nr:true,nc:true},
  {t:'→',m:'hold',d:{type:'key_down',key:'right'},u:{type:'key_up',key:'right'},nr:true,nc:true},
];
var _mc=document.getElementById('mc');_MOUSE.forEach(function(s){_mc.appendChild(_mkBtn(s));});
_KEYS.forEach(function(s){ks.appendChild(_mkBtn(s));});
(function(){
  var sh=document.createElement('button');sh.className='k';sh.textContent='SHIFT';
  // place SHIFT right after the Tab key (tap key) in the keyboard row
  var tabBtn=null;
  for(var i=0;i<ks.children.length;i++){
    if(ks.children[i].textContent==='Tab'){tabBtn=ks.children[i];break;}
  }
  if(tabBtn)ks.insertBefore(sh,tabBtn.nextSibling);else ks.appendChild(sh);
  var shT=null;
  _gShRel2=function(){
    if(_gShSt||shT){_gShSt=false;if(shT){clearTimeout(shT);shT=null;}sh.classList.remove('sh-on');sendCmd({type:'key_up',key:'shift'});}
  };
  var d=function(){
    if(_gShSt){_gShRel2();return;}
    sendCmd({type:'key_down',key:'shift'});
    shT=setTimeout(function(){shT=null;_gShSt=true;sh.classList.add('sh-on');},500);
  };
  var u=function(){
    if(shT){clearTimeout(shT);shT=null;sendCmd({type:'key_up',key:'shift'});return;}
    if(_gShSt)sh.classList.add('sh-on');
  };
  sh.addEventListener('mousedown',d);sh.addEventListener('mouseup',u);sh.addEventListener('mouseleave',u);
  sh.addEventListener('touchstart',function(e){e.preventDefault();d();});
  sh.addEventListener('touchend',function(e){e.preventDefault();u();});
})();
var cv=document.getElementById('cv'),sw=document.getElementById('sw'),ta=document.getElementById('ta'),ti=document.getElementById('ti');
var st=document.getElementById('st');
document.addEventListener('mousedown',function(e){if(e.target.closest('.k,.send,.rec,.rec-mini,.ctl'))e.preventDefault();});
let ws=null,wsV=null,_so=false,_dm=false;
var _px=0,_py=0,_mv=false,_ts=0,_vfps=30,_fts=[],_qmtx=0,_qmty=0,_qmp=false;
var _mdP=false,_mdX=0,_mdY=0;
var decoder=null;
var _pc=null,_rtcDc=null;
var _rw=0,_rh=0,_rwr=0,_rhr=0,_pinching=false,_pinchEnded=false,_ps=0,_pcx=0,_pcy=0,_pzc=0;
var _iceServers=[];
var mb=document.getElementById('mb');
/* Render queue: decoded frames are buffered (pre-fill, cap 6) and drawn on
   an absolute fractional schedule that matches the capture rate exactly, so
   the buffer neither drains (causing gaps to freeze) nor grows (causing frame
   drops).  Pre-fill is fixed at 1 frame to keep end-to-end latency minimal. */
var _rq=[],_rqTimer=null,_rqPre=1,_rqMax=6,_rqFirst=false,_stLast=0;
function _rqStop(){
  if(_rqTimer){clearInterval(_rqTimer);_rqTimer=null;}
  _rqFirst=false;
  for(var i=0;i<_rq.length;i++){try{_rq[i].close();}catch(_){}}
  _rq=[];
  _latReset();
}
function _rqStart(){
  if(_rqTimer||!_vfps)return;
  var iv=1000/_vfps;  // fractional frame interval (e.g. 18.18ms at 55fps)
  var next=0;
  _rqTimer=setInterval(function(){
    if(!_rqFirst){
      if(_rq.length<_rqPre)return;
      _rqFirst=true;
      next=performance.now()+iv;
    }
    var now=performance.now();
    if(now<next)return;
    if(_rq.length){
      var f=_rq.shift();
      try{
        var tg=_gm?document.getElementById('gc'):cv;
        tg.getContext('2d').drawImage(f,0,0);
      }catch(_){}
      try{f.close();}catch(_){}
    }
    next+=iv;                 // fractional absolute schedule
    if(next<now-iv)next=now;  // resync if a tick fell behind
  },8);
}
function sm(on){
  _so=on;mb.textContent=on?'SCREEN':'REMOTE';mb.className='mode-btn'+(on?' on':'');document.body.classList.toggle('screen',on);
  if(on){
    sw.style.overflow='auto';document.getElementById('ph').style.display='none';
    document.getElementById('pz').style.display='';document.getElementById('ph').innerHTML='';_fts=[];
    if(wsV&&wsV.readyState===WebSocket.OPEN){
      if(typeof RTCPeerConnection!=='undefined'&&typeof VideoDecoder==='undefined'){
        wsV.send(JSON.stringify({type:'set_mode',screen:true,format:'webrtc'}));
        var td=document.createElement('div');
        td.style.cssText='position:fixed;left:50%;top:12px;transform:translateX(-50%);background:rgba(0,0,0,.85);color:#ffd166;border:1px solid #ffd166;padding:6px 14px;font-size:11px;letter-spacing:.05em;z-index:2000;border-radius:3px;font-family:inherit;pointer-events:none;white-space:nowrap';
        td.textContent='当前浏览器无 VideoDecoder,使用 WebRTC 兜底(30fps+高延迟)。建议 Chrome/Edge';
        document.body.appendChild(td);
        setTimeout(function(){try{td.remove()}catch(_){}},5000);
      }
      else
        wsV.send(JSON.stringify({type:'set_mode',screen:true}));
    }
  }else{
    _fts=[];st.textContent='LIVE';_rqStop();
    if(decoder){try{decoder.close();}catch(e){}decoder=null;}
    if(_pc){try{_pc.close();}catch(e){}_pc=null;}_rtcDc=null;
    var vd=document.getElementById('vd');if(vd){vd.style.display='none';}
    cv.style.display='none';document.getElementById('ph').style.display='';
    document.getElementById('ph').innerHTML='<div class=ph>REMOTE MODE</div>';
    document.getElementById('pz').style.display='none';
    if(document.body.classList.contains('full'))zs(Z.length-2);
    if(wsV&&wsV.readyState===WebSocket.OPEN)wsV.send(JSON.stringify({type:'set_mode',screen:false}));
  }
}
mb.addEventListener('click',function(){sm(!_so);});
function _openPanel(){
  var d=document.createElement('div');
  d.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.7);display:flex;align-items:center;justify-content:center;z-index:1001';
  var b=document.createElement('div');
  b.style.cssText='background:var(--surface);border:1px solid var(--border);padding:16px;display:flex;flex-direction:column;gap:8px;min-width:220px';
  var t=document.createElement('div');t.textContent='DeskBeam';
  t.style.cssText='color:var(--dim);font-size:10px;letter-spacing:.1em;text-transform:uppercase;text-align:center;margin-bottom:4px';
  b.appendChild(t);
  var row=document.createElement('div');row.style.cssText='display:flex;gap:8px';
  b.appendChild(row);
  var l=document.createElement('button');l.textContent='Logout';
  l.style.cssText='flex:1;height:36px;border:1px solid var(--blue);background:var(--surface);color:var(--blue);font-family:inherit;font-size:12px;cursor:pointer';
  l.onclick=function(){d.remove();location.href=_BASE+'/logout'};
  row.appendChild(l);
  var s=document.createElement('button');s.textContent='Shutdown';
  s.style.cssText='flex:1;height:36px;border:1px solid var(--accent);background:var(--surface);color:var(--accent);font-family:inherit;font-size:12px;cursor:pointer';
  s.onclick=function(){d.remove();location.href=_BASE+'/shutdown'};
  row.appendChild(s);
  var g=document.createElement('button');g.textContent=_gm?'GYRO ON':'GYRO';
  g.style.cssText='height:36px;border:1px solid var(--green);background:var(--surface);color:var(--green);font-family:inherit;font-size:12px;cursor:pointer';
  g.onclick=function(){d.remove();_gyToggle();};
  b.appendChild(g);
  var th=document.createElement('button');
  th.textContent=document.documentElement.classList.contains('dark')?'LIGHT':'DARK';
  th.style.cssText='height:36px;border:1px solid var(--border);background:var(--surface);color:var(--dim);font-family:inherit;font-size:12px;cursor:pointer';
  th.onclick=function(){d.remove();_toggleTheme()};
  b.appendChild(th);
  var c=document.createElement('button');c.textContent='Cancel';
  c.style.cssText='height:36px;border:1px solid var(--border);background:var(--surface);color:var(--dim);font-family:inherit;font-size:12px;cursor:pointer';
  c.onclick=function(){d.remove()};
  b.appendChild(c);
  d.appendChild(b);
  document.body.appendChild(d);
}
var _zi=0,Z=['Fit','Full'],ZF=[0,-1];
function zs(i){
  _zi=Math.max(0,Math.min(Z.length-1,i));var z=ZF[_zi];
  document.body.classList.toggle('full',z<0);document.body.classList.remove('pinching');
  var vd=document.getElementById('vd');
  if(z<0){
    cv.style.width='100%';cv.style.height='100%';cv.style.maxWidth='none';cv.style.maxHeight='none';
    if(vd){vd.style.width='100%';vd.style.height='100%';vd.style.maxWidth='none';vd.style.maxHeight='none';}
  }else if(z===0){
    cv.style.width='';cv.style.height='';cv.style.maxWidth='100%';cv.style.maxHeight='100%';
    if(vd){vd.style.width='';vd.style.height='';vd.style.maxWidth='100%';vd.style.maxHeight='';}
  }else{
    cv.style.maxWidth='none';cv.style.maxHeight='none';cv.style.width=(z*100)+'%';cv.style.height='';
    if(vd){vd.style.maxWidth='none';vd.style.maxHeight='none';vd.style.width=(z*100)+'%';vd.style.height='';}
  }
  document.getElementById('zl').textContent=Z[_zi];
}
document.getElementById('zi').addEventListener('click',function(){zs(_zi+1);});document.getElementById('zo').addEventListener('click',function(){zs(_zi-1);});zs(0);
var P=60;
document.getElementById('pl').addEventListener('click',function(){sw.scrollLeft=Math.max(0,sw.scrollLeft-P);});
document.getElementById('pr').addEventListener('click',function(){sw.scrollLeft=Math.min(sw.scrollWidth-sw.clientWidth,sw.scrollLeft+P);});
document.getElementById('pu').addEventListener('click',function(){sw.scrollTop=Math.max(0,sw.scrollTop-P);});
document.getElementById('pd').addEventListener('click',function(){sw.scrollTop=Math.min(sw.scrollHeight-sw.clientHeight,sw.scrollTop+P);});
function cn(){
  var p=location.protocol==='https:'?'wss':'ws';
  ws=new WebSocket(p+'://'+location.host+_BASE+'/ws_cmd');
  ws.onopen=function(){
    st.textContent='LIVE';
    var gx=document.getElementById('gex');if(gx)gx.textContent='LIVE';
  };
  ws.onclose=function(){
    _qmtx=0;_qmty=0;_qmp=false;st.textContent='RETRY';
    var gx=document.getElementById('gex');if(gx)gx.textContent='RETRY';
    setTimeout(cn,2000);
  };
  ws.onerror=function(){st.textContent='ERROR';};
  ws.onmessage=function(e){
    if(typeof e.data!=='string')return;
    try{
      var m=JSON.parse(e.data);
      if(m.type==='hello'){
        _iceServers=m.iceServers||[];_isLan=m.lan!==false;
        if(!m.streaming){document.getElementById('mb').style.display='none';}
        if(m.espConfig){
          _espRelay=m.espConfig.relayUrl;_espToken=m.espConfig.token;_espDev=m.espConfig.device;
        }
      }
    }catch(_){}
  };
}
function cnV(){
  var p=location.protocol==='https:'?'wss':'ws';
  wsV=new WebSocket(p+'://'+location.host+_BASE+'/ws');
  wsV.binaryType='arraybuffer';
  wsV.onopen=function(){if(_so||_gm)sm(true);};
  wsV.onclose=function(){setTimeout(cnV,2000);};
  wsV.onerror=function(){};
  wsV.onmessage=function(e){
    if(typeof e.data==='string'){
      try{
        var m=JSON.parse(e.data);
        if(m.type==='screen_config'){
          if(typeof VideoDecoder==='undefined')return;
          _rqStop();_rw=m.width;_rh=m.height;_rwr=m.raw_width||_rw;_rhr=m.raw_height||_rh;_ts=0;_vfps=m.fps||30;_rqPre=1;_latIv=1000/_vfps;
          if(decoder){try{decoder.close();}catch(ex){}decoder=null;}
          var vd=document.getElementById('vd');if(vd)vd.style.display='none';
          var tg=_gm?document.getElementById('gc'):cv;
          tg.style.display='';tg.width=_rw;tg.height=_rh;
          decoder=new VideoDecoder({
            output:function(f){
              var n=performance.now();_fts.push(n);_fts.length>10&&_fts.shift();
              var _nw=performance.now();
              if(_nw-_stLast>500){
                _stLast=_nw;
                var fps=_fts.length<3?0:Math.min(120,Math.round((_fts.length-1)*1000/(_fts[_fts.length-1]-_fts[0])));
                st.textContent='LIVE '+String(fps).padStart(3,'0')+'fps '+String(Math.max(0,Math.round(_lat))).padStart(3,'0')+'ms';
              }
              _rq.push(f);
              if(_rq.length>_rqMax){try{_rq.shift().close();}catch(_){}}
              _rqStart();
            },
            error:function(e){console.error('Decoder:',e);}
          });
          decoder.configure({codec:m.codec});_fts=[];
        }else if(m.type==='webrtc_offer'){
          var pc=new RTCPeerConnection({iceServers:_iceServers});_pc=pc;_rtcDc=null;
          pc.ontrack=function(e){
            var tv=_gm?document.getElementById('gv'):document.getElementById('vd');
            if(_gm){document.getElementById('gc').style.display='none';}
            else{cv.style.display='none';}
            tv.style.display='block';tv.srcObject=e.streams[0];
          };
          pc.ondatachannel=function(e){_rtcDc=e.channel;};
          pc.setRemoteDescription(new RTCSessionDescription({sdp:m.sdp,type:m.sdp_type})).then(function(){return pc.createAnswer()}).then(function(a){
            pc.setLocalDescription(a);wsV.send(JSON.stringify({type:'webrtc_answer',sdp:a.sdp,sdp_type:a.type}));
          });
          pc.onicecandidate=function(e){
            if(e.candidate&&wsV)wsV.send(JSON.stringify({type:'webrtc_ice',candidate:{candidate:e.candidate.candidate,sdpMid:e.candidate.sdpMid||'0',sdpMLineIndex:e.candidate.sdpMLineIndex||0}}));
          };
        }else if(m.type==='webrtc_ice'){
          if(_pc){try{_pc.addIceCandidate(new RTCIceCandidate(m.candidate))}catch(ex){}}
        }
      }catch(_){}
    }else if(e.data instanceof ArrayBuffer){
      var v=new Uint8Array(e.data);
      if(v.length<5)return;
      var isKey=v[0]===1,h264=v.subarray(5);
      var seq=0;
      for(var i=0;i<4;i++)seq=seq*256+v[1+i];
      _latMeasure(seq);
      if(decoder&&decoder.state==='configured'){
        var c=new EncodedVideoChunk({type:isKey?'key':'delta',timestamp:_ts,data:h264});_ts+=Math.round(1000000/_vfps);
        try{decoder.decode(c);}catch(_){}
      }
    }
  };
}

/* 端到端延迟:服务端每帧带一个单调帧序号。客户端只用本地时钟
   performance.now()——不比较两端时钟(跨机器时钟速率不同,直接相减
   会产生单向漂移)。帧 N 期望到达 = 首帧到达时刻 + N * 帧间隔,帧间隔
   直接用服务端上报的 fps(1000/fps)计算,不靠客户端测量到达间隔
   (浏览器事件积压会让实测间隔失真)。实际到达晚于期望多少,就是累积
   延迟。持续超限就重连视频流释放;控制通道保留。 */
var _latS0=-1,_latA0=0,_latLast=-1;
var _latIv=1000/30;
var _lat=0,_latOver=null,_latLastRefresh=0;
var _LAT_LIMIT=1500;      // 触发阈值 ms
var _LAT_COOLDOWN=60000;  // 一次刷新后的冷却 ms
function _latReset(){
  _latS0=-1;_latA0=0;_latLast=-1;
  _lat=0;_latOver=null;
}
function _latMeasure(seq){
  var now=performance.now();
  if(_latS0<0){_latS0=seq;_latA0=now;_latLast=seq;_lat=0;_latOver=null;return;}
  if(seq!==_latLast+1){_latS0=seq;_latA0=now;_latLast=seq;_lat=0;_latOver=null;return;}
  _latLast=seq;
  _lat=now-(_latA0+(seq-_latS0)*_latIv);
  if(_lat>_LAT_LIMIT){
    if(_latOver===null)_latOver=now;
    else if(now-_latOver>500&&now-_latLastRefresh>_LAT_COOLDOWN){
      _latOver=null;_latLastRefresh=now;_latReset();
      if(wsV)wsV.close();
    }
  }else _latOver=null;
}

var _tt=null,_lt=0;
ta.addEventListener('touchstart',function(e){
  if(_tt){clearTimeout(_tt);_tt=null;}
  _pinchEnded=false;
  if(e.targetTouches.length===2&&_so){
    _pinching=true;_ps=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);
    _pcx=(e.touches[0].clientX+e.touches[1].clientX)/2;_pcy=(e.touches[0].clientY+e.touches[1].clientY)/2;
    var _cp=cv.style.width;_pzc=_cp?Math.max(0.5,Math.min(10,parseFloat(_cp)/100)):1;
    if(document.body.classList.contains('full')){document.body.classList.add('pinching');cv.style.height='';}
    return;
  }
  var t=e.touches[0];_px=t.clientX;_py=t.clientY;_mv=false;if(_dm)sendCmd({type:'mouse_down'});
},{passive:false});
ta.addEventListener('touchmove',function(e){
  e.preventDefault();
  if(_pinching&&e.targetTouches.length===2){
    var ds=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);
    var cx=(e.touches[0].clientX+e.touches[1].clientX)/2,cy=(e.touches[0].clientY+e.touches[1].clientY)/2;
    var nz=Math.max(document.body.classList.contains('full')?1:0.5,Math.min(10,_pzc*ds/_ps));
    var oz=parseFloat(cv.style.width)/100||1;
    cv.style.maxWidth='none';cv.style.maxHeight='none';cv.style.width=Math.round(nz*100)+'%';
    sw.scrollLeft=Math.round((cx+sw.scrollLeft)*nz/oz-cx);sw.scrollTop=Math.round((cy+sw.scrollTop)*nz/oz-cy);
    sw.scrollLeft-=cx-_pcx;sw.scrollTop-=cy-_pcy;_pcx=cx;_pcy=cy;return;
  }
  var t=e.touches[0],dx=Math.round((t.clientX-_px)*4),dy=Math.round((t.clientY-_py)*4);
  if(Math.abs(dx)<2&&Math.abs(dy)<2)return;_px=t.clientX;_py=t.clientY;_mv=true;
  _mqAcc(dx,dy);
},{passive:false});
ta.addEventListener('touchend',function(e){
  if(_pinching){
    if(e.targetTouches.length<2){
      _pinching=false;_pinchEnded=true;
      if(e.targetTouches.length===1){var _t=e.targetTouches[0];_px=_t.clientX;_py=_t.clientY;}
      if(document.body.classList.contains('full')&&Math.abs((parseFloat(cv.style.width)||100)-100)<0.5)document.body.classList.remove('pinching');
    }
    return;
  }
  if(_pinchEnded){_pinchEnded=false;return;}
  _te=Date.now();
  if(_dm)sendCmd({type:'mouse_up'});
  else if(!_mv){
    _tt=setTimeout(function(){
      _tt=null;
      if(Date.now()-_lt<500){sendCmd({type:'mouse_double_click'});_lt=0;return;}
      if(document.body.classList.contains('full')){sendCmd({type:'mouse_click'});}
      else if(_rw&&_rh&&cv.style.display!=='none'&&cv.getBoundingClientRect){
        var t=e.changedTouches[0],rect=cv.getBoundingClientRect(),ix=rect.left,iy=rect.top,iw=rect.width,ih=rect.height;
        if(t.clientX>=ix&&t.clientX<=ix+iw&&t.clientY>=iy&&t.clientY<=iy+ih)
          sendCmd({type:'mouse_click_at',x:Math.round((t.clientX-ix)/iw*_rwr),y:Math.round((t.clientY-iy)/ih*_rhr)});
        else sendCmd({type:'mouse_click'});
      }else sendCmd({type:'mouse_click'});
      _lt=Date.now();
    },20);
  }
},{passive:false});
ta.addEventListener('mousemove',function(e){var dx=e.movementX||0,dy=e.movementY||0;if(_gy)return;if(_mdP&&(Math.abs(e.clientX-_mdX)>5||Math.abs(e.clientY-_mdY)>5))_mdP=false;if(!(e.buttons&1))return;if(!dx&&!dy)return;_mqAcc(dx*4,dy*4);});
ta.addEventListener('mousedown',function(e){if(e.button!==0)return;ta.focus();_mdP=true;_mdX=e.clientX;_mdY=e.clientY;});
ta.addEventListener('mouseup',function(e){var wasClick=_mdP&&e.button===0;_mdP=false;if(wasClick&&Date.now()-_te>300){sendCmd({type:'mouse_down'});sendCmd({type:'mouse_up'});}});
function sendCmd(obj){
  var s=JSON.stringify(obj);
  if(_rtcDc&&_rtcDc.readyState==='open')_rtcDc.send(s);
  else if(ws&&ws.readyState===WebSocket.OPEN)ws.send(s);
  if(_rtcDc&&(_rtcDc.readyState==='closing'||_rtcDc.readyState==='closed'))_rtcDc=null;
}
var _gy=false,_gsX=20,_gsY=15,_gyLast=0,_gm=false,_gPrevSo=false,_gAcc=true;
var _gShSt=false,_gShRel=null,_gShRel2=null;
function _gSetAcc(on){
  _gAcc=on;
  var b=document.getElementById('ggb');
  if(b){if(on)b.classList.add('on');else b.classList.remove('on');}
  if(_gm)sendCmd({type:'set_gyro',on:!on});
}
function _gyStart(){
  _gy=true;
  sendCmd({type:'set_gyro',on:!_gAcc});
  var s=document.getElementById('gyroStatus');if(s){s.textContent='GYRO ON';s.classList.remove('off');}
}
function _gyStop(){
  _gy=false;
  sendCmd({type:'set_gyro',on:false});
  var s=document.getElementById('gyroStatus');if(s){s.textContent='GYRO OFF';s.classList.add('off');}
}
function _gyToggleSensor(){
  if(_gy){_gyStop();}else{_gyStart();}
}
function _gyRequest(cb){
  var req=window.DeviceMotionEvent&&window.DeviceMotionEvent.requestPermission;
  if(req){req().then(function(r){if(r==='granted')cb();else{alert('陀螺仪权限被拒绝');}}).catch(function(){});}
  else if(window.DeviceMotionEvent){cb();}
  else{alert('此设备不支持陀螺仪');}
}
function _gyEnter(){
  _gyRequest(function(){
    _gm=true;_gPrevSo=_so;
    if(document.body.classList.contains('full'))zs(Z.length-2);
    if(_dm){_dm=false;sendCmd({type:'mouse_up'});}
    document.getElementById('gyroPage').classList.add('on');
    var ggb=document.getElementById('ggb');
    if(ggb){if(_gAcc)ggb.classList.add('on');else ggb.classList.remove('on');}
    _gyStart();
    var cb=document.getElementById('gcb');
    sm(true);
    if(_so){cb.classList.add('on');}else{cb.classList.remove('on');}
    _gyShowCam(_so);
    _gySetView(_so);
  });
}
function _gyExit(){
  _gm=false;
  if(_gtt===1){sendCmd({type:'mouse_up'});_gtt=0;}
  if(_gShRel)_gShRel();
  if(_gShRel2)_gShRel2();
  _gHoldUp.forEach(function(h){
    if(h.el.classList.contains('pressed')){h.el.classList.remove('pressed');sendCmd(h.up);}
  });
  _gyStop();
  document.getElementById('gyroPage').classList.remove('on');
  document.getElementById('gcb').classList.remove('on');
  _gySetView(true);
  sm(_gPrevSo);
}
function _gyToggle(){
  if(_gm){_gyExit();return;}
  _gyEnter();
}
function _gyShowCam(on){
  var msg=document.getElementById('gmsg');
  var gv=document.getElementById('gv'),gc=document.getElementById('gc');
  if(on){
    msg.style.display='none';
    if(gv&&gv.style.display!=='none'){gc.style.display='none';gv.style.display='';}
    else{
      if(_rw&&gc.width!==_rw){gc.width=_rw;gc.height=_rh;}
      gc.style.display='';
    }
  }else{
    msg.style.display='';
    if(gv){gv.style.display='none';}
    if(gc){gc.style.display='none';}
  }
}

function _gyCalib(){
  sendCmd({type:'gyro_calib'});
}
var _gLand=false,_gAxSwap=false,_gyOrientT=null;
function _gAxBtn(){
  var b=document.getElementById('gax');
  if(!b)return;
  b.textContent=((_gLand^_gAxSwap)?'L':'P')+(_gAxSwap?'*':'');
  b.classList.toggle('on',_gAxSwap);
}
function _gySetLand(land){
  _gLand=land;_gAxSwap=false;_gAxBtn();
}
function _gyOrient(){_gySetLand(window.matchMedia&&window.matchMedia('(orientation:landscape)').matches);_gLayout();
  clearTimeout(_gyOrientT);
  _gyOrientT=setTimeout(function(){if(_gm)_gyCalib();},300);}
_gyOrient();
if(window.matchMedia)window.matchMedia('(orientation:landscape)').addListener(function(m){_gySetLand(m.matches);});
window.addEventListener('orientationchange',function(){setTimeout(_gyOrient,200);});
window.addEventListener('devicemotion',function(e){
  if(!_gy)return;
  var rr=e.rotationRate;
  if(!rr||rr.gamma==null)return;
  var now=Date.now();
  var dt=Math.min(0.05,(now-_gyLast)/1000);
  _gyLast=now;
  if(dt<=0)return;
  var ga=rr.alpha!=null?rr.alpha:0;
  var gy=rr.gamma;
  var gb=rr.beta!=null?rr.beta:0;
  var dead=1.0;
  var dx=0,dy=0;
  if(_gLand^_gAxSwap){
    if(Math.abs(gy)>dead)dx=Math.round(-gy*dt*(_gsX)*2);
    if(Math.abs(gb)>dead)dy=Math.round(gb*dt*(_gsY)*2);
  }else{
    if(Math.abs(gy)>dead)dx=Math.round(-gy*dt*(_gsX)*2);
    if(Math.abs(ga)>dead)dy=Math.round(-ga*dt*(_gsY)*2);
  }
  if(dx||dy){
    if(_isLan)sendCmd({type:'mouse_move',dx:dx,dy:dy});
    else _mqAcc(dx,dy);
  }
});
var _espRelay='',_espToken='',_espDev='';
function _espHid(k){
  if(!_espRelay)return;
  var r=new WebSocket(_espRelay);
  r.onopen=function(){
    r.send(JSON.stringify({type:'register',token:_espToken}));
    r.send(JSON.stringify({type:'hid_key',device:_espDev,key:k}));
    setTimeout(function(){try{r.close()}catch(_){}},500);
  };
}
function _espUac(){_espHid('left');setTimeout(function(){_espHid('enter')},200);}
function _espJs(){var s=['backspace','0','5','1','0','2','2'];function n(i){if(i>=s.length)return;_espHid(s[i]);setTimeout(function(){n(i+1)},i===0?10000:200);}n(0);}
function _espEsc(){_espHid('esc');}
function _espLock(){_espHid('win+l');}
function _espWin(){_espHid('win');}
function _espMouse(o){
  if(!_espRelay)return;
  var r=new WebSocket(_espRelay);
  r.onopen=function(){
    var m={type:'hid_mouse',device:_espDev};
    if(o.btn)m.btn=o.btn;
    if(o.x)m.x=o.x;
    if(o.y)m.y=o.y;
    if(o.w)m.w=o.w;
    if(o.hold)m.hold=o.hold;
    r.send(JSON.stringify({type:'register',token:_espToken}));
    r.send(JSON.stringify(m));
    setTimeout(function(){try{r.close()}catch(_){}},500);
  };
}
function _espL(){_espMouse({btn:1});}
document.addEventListener('touchmove',function(e){if(!e.target.closest('#ks')&&!e.target.closest('input'))e.preventDefault();},{passive:false});
function _bindHoldEl(el,dcmd,ucmd,nocancel,norepeat){
  if(!el)return;
  var iv=null,to=null,pr=false,sx=0,sy=0,sl=false,sTid=-1;
  var add=function(){el.classList.add('pressed');};
  var rem=function(){el.classList.remove('pressed');};
  var sd=function(){if(Array.isArray(dcmd))dcmd.forEach(function(c){sendCmd(c);});else sendCmd(dcmd);};
  var su=function(){if(!ucmd)return;if(Array.isArray(ucmd))ucmd.forEach(function(c){sendCmd(c);});else sendCmd(ucmd);};
  var _tid=function(ts){var t;for(var i=0;i<ts.length;i++)if(ts[i].identifier===sTid){t=ts[i];break;}return t||ts[0];};
  var down=function(e){
    if(e.type==='touchstart'){
      var t=e.changedTouches[0];sx=t.clientX;sy=t.clientY;sTid=t.identifier;sl=false;
      if(pr)return;pr=true;add();sd();
      if(!norepeat)to=setTimeout(function(){to=null;iv=setInterval(sd,50);},350);
    }else{
      e.preventDefault();
      if(pr)return;pr=true;add();sd();
      if(!norepeat)to=setTimeout(function(){to=null;iv=setInterval(sd,50);},350);
    }
  };
  var mv=function(e){
    if(sl)return;
    if(nocancel)return;
    var t=_tid(e.touches);
    if(t.identifier!==sTid)return;
    if(Math.abs(t.clientX-sx)>10||Math.abs(t.clientY-sy)>10){
      sl=true;
      if(pr){pr=false;rem();if(to){clearTimeout(to);to=null;}if(iv){clearInterval(iv);iv=null;}su();}
    }
  };
  var up=function(e){
    if(e.type==='touchend'&&sl)return;
    e.preventDefault();sTid=-1;rem();
    if(!pr)return;pr=false;
    if(to){clearTimeout(to);to=null;}
    if(iv){clearInterval(iv);iv=null;}
    su();
  };
  _gHoldUp.push({el:el,up:su});
  if(!_isTouch){el.addEventListener('mousedown',down);el.addEventListener('mouseup',up);el.addEventListener('mouseleave',up);}
  el.addEventListener('touchstart',down,{passive:true});
  el.addEventListener('touchmove',mv,{passive:true});
  el.addEventListener('touchend',up,{passive:false});
  el.addEventListener('touchcancel',up);
}
function _bindHoldCombo(el,keys){if(!el)return;_bindHoldEl(el,keys.map(function(k){return {type:'key_down',key:k};}),keys.map(function(k){return {type:'key_up',key:k};}),true,true);}
function _bindRepEl(el,cmd){if(!el)return;
  var iv=null,to=null,pr=false,sx=0,sy=0,sl=false,sTid=-1;
  var add=function(){el.classList.add('pressed');};
  var rem=function(){el.classList.remove('pressed');};
  var _tid=function(ts){var t;for(var i=0;i<ts.length;i++)if(ts[i].identifier===sTid){t=ts[i];break;}return t||ts[0];};
  var f=function(e){
    if(e.type==='touchstart'){
      var t=e.changedTouches[0];sx=t.clientX;sy=t.clientY;sTid=t.identifier;sl=false;
      if(pr)return;pr=true;add();sendCmd({type:cmd});
      to=setTimeout(function(){to=null;iv=setInterval(function(){sendCmd({type:cmd});},80);},350);
    }else{
      e.preventDefault();
      if(pr)return;pr=true;add();sendCmd({type:cmd});
      to=setTimeout(function(){to=null;iv=setInterval(function(){sendCmd({type:cmd});},80);},350);
    }
  };
  var mv=function(e){
    if(sl)return;
    var t=_tid(e.touches);
    if(t.identifier!==sTid)return;
    if(Math.abs(t.clientX-sx)>10||Math.abs(t.clientY-sy)>10){
      sl=true;
      if(pr){pr=false;rem();if(to){clearTimeout(to);to=null;}if(iv){clearInterval(iv);iv=null;}}
    }
  };
  var g=function(e){
    if(e.type==='touchend'&&sl)return;
    e.preventDefault();sTid=-1;rem();
    if(!pr)return;pr=false;
    if(to){clearTimeout(to);to=null;}
    if(iv){clearInterval(iv);iv=null;}
  };
  if(!_isTouch){el.addEventListener('mousedown',f);el.addEventListener('mouseup',g);el.addEventListener('mouseleave',g);}
  el.addEventListener('touchstart',f,{passive:true});
  el.addEventListener('touchmove',mv,{passive:true});
  el.addEventListener('touchend',g,{passive:false});
  el.addEventListener('touchcancel',g);
}
function _tapEl(el,fn,nocancel){if(!el)return;
  var sx=0,sy=0,sl=false,sTid=-1;
  var add=function(){el.classList.add('pressed');};
  var rem=function(){el.classList.remove('pressed');};
  var _tid=function(ts){var t;for(var i=0;i<ts.length;i++)if(ts[i].identifier===sTid){t=ts[i];break;}return t||ts[0];};
  var f=function(e){
    if(e.type==='touchstart'){
      var t=e.changedTouches[0];sx=t.clientX;sy=t.clientY;sTid=t.identifier;sl=false;add();return;
    }
    if(sl){rem();return;}
    e.preventDefault();rem();fn.call(el);
  };
  var mv=function(e){
    if(sl)return;
    if(nocancel)return;
    var t=_tid(e.touches);
    if(t.identifier!==sTid)return;
    if(Math.abs(t.clientX-sx)>10||Math.abs(t.clientY-sy)>10)sl=true;
  };
  if(!_isTouch)el.addEventListener('mousedown',f);
  el.addEventListener('touchstart',f,{passive:true});
  el.addEventListener('touchmove',mv,{passive:true});
  el.addEventListener('touchend',f,{passive:false});
}
var sn=document.getElementById('sn'),rc=document.getElementById('rc'),rr=document.getElementById('rr');
sn.addEventListener('click',function(){var t=ti.value;if(!t)return;sendCmd({type:'type_text',text:t.replace(/\n/g,' ')});ti.value='';ti.focus();});
var _rr=false,_ac=null,_st=null,_ch=[];var _te=0,_ci=null;
var _pxc=null,_pxb=false;
var _awUrl=URL.createObjectURL(new Blob(
  ["class P extends AudioWorkletProcessor{process(i,o){const c=i[0][0];for(let n=0;n<c.length;n++)o[0][0][n]=c[n];this.port.postMessage(new Float32Array(c));return true}}registerProcessor('r',P);"],
  {type:'application/javascript'}
));
function _warmCtx(){
  if(_pxc||_pxb)return;
  _pxb=true;
  var ac=new(window.AudioContext||window.webkitAudioContext)({sampleRate:16000});
  ac.audioWorklet.addModule(_awUrl).then(function(){_pxc=ac;_pxb=false;}).catch(function(){_pxb=false;});
}
document.addEventListener('touchstart',_warmCtx,{once:true});document.addEventListener('mousedown',_warmCtx,{once:true});
function wv(s,sr){
  var b=new ArrayBuffer(44+s.length*2),v=new DataView(b);
  var w=function(p,s){for(var i=0;i<s.length;i++)v.setUint8(p+i,s.charCodeAt(i));};
  w(0,'RIFF');v.setUint32(4,36+s.length*2,true);w(8,'WAVE');w(12,'fmt ');
  v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);
  v.setUint32(24,sr,true);v.setUint32(28,sr*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);
  w(36,'data');v.setUint32(40,s.length*2,true);
  for(var i=0;i<s.length;i++)v.setInt16(44+i*2,Math.round(Math.max(-1,Math.min(1,s[i]))*32767),true);
  return new Blob([b],{type:'audio/wav'});
}
function _fc(){
  var l=_ch.reduce(function(s,c){return s+c.length;},0);
  if(!l)return;
  var sa=new Float32Array(l),off=0;
  _ch.forEach(function(c){sa.set(c,off);off+=c.length;});
  if(ws&&ws.readyState===WebSocket.OPEN){_ch=[];ws.send(wv(sa,16000));}
}
function rs(){
  if(_rr)return;
  _rr=true;_ch=[];_ci=setInterval(_fc,2000);
  _recBtn.textContent=_recBtn.classList.contains('rec-mini')?'S':'\u25a0 Stop';
  _recBtn.parentElement.classList.add('recording');
  navigator.mediaDevices.getUserMedia({audio:true}).then(function(s){
    if(!_rr){s.getTracks().forEach(function(t){t.stop();});return}
    _st=s;
    if(_pxc&&_pxc.state==='suspended')_pxc.resume();
    _ac=_pxc||new(window.AudioContext||window.webkitAudioContext)({sampleRate:16000});
    var src=_ac.createMediaStreamSource(_st);
    var load=_pxc?Promise.resolve():_ac.audioWorklet.addModule(_awUrl);
    load.then(function(){
      var nd=new AudioWorkletNode(_ac,'r',{channelCount:1});
      nd.port.onmessage=function(e){if(_rr)_ch.push(e.data);};
      src.connect(nd);
    }).catch(function(){re(true);});
  }).catch(function(){re(true);});
}
function re(si){
  if(!_rr)return;
  _rr=false;
  if(_ci){clearInterval(_ci);_ci=null;}
  _fc();
  if(ws&&ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'voice_end'}));
  _recBtn.textContent=_recBtn.classList.contains('rec-mini')?'R':'REC';
  _recBtn.parentElement.classList.remove('recording');
  if(_st){_st.getTracks().forEach(function(t){t.stop();});_st=null;}
  if(_ac)_ac.close();
  _ac=null;_pxc=null;_pxb=false;
  setTimeout(_warmCtx,0);
}
var _pt=0,_wr=false;
function _rcD(){_pt=Date.now();_wr=_rr;rs();}
function _rcU(){if(!_rr||_pt===0)return;if(_wr||Date.now()-_pt>=300)re();}
function _bindRec(b){
  b.addEventListener('mousedown',function(){_recBtn=b;_rcD();});
  b.addEventListener('mouseup',_rcU);
  b.addEventListener('mouseleave',function(){if(_rr)re();});
  b.addEventListener('touchstart',function(e){e.preventDefault();_recBtn=b;_rcD();});
  b.addEventListener('touchend',function(e){e.preventDefault();_rcU();});
  b.addEventListener('touchcancel',function(e){e.preventDefault();if(_rr)re();});
}
var _recBtn=rc;_bindRec(rc);
var bs=document.getElementById('bs');_bindHoldEl(bs,{type:'scroll_up'},null,true);
var be=document.getElementById('be');_bindHoldEl(be,{type:'mouse_right_down'},{type:'mouse_right_up'},true,true);
_bindHoldEl(rr,{type:'scroll_down'},null,true);
ti.addEventListener('focus',function(){
  if(!document.getElementById('sw-spacer')){
    var s=document.createElement('div');s.id='sw-spacer';s.style.cssText='height:400px;flex:0 0 auto;';sw.appendChild(s);
  }
});
ti.addEventListener('blur',function(){var s=document.getElementById('sw-spacer');if(s)s.remove();});
function _toggleTheme(){var h=document.documentElement;h.classList.toggle('dark');localStorage.setItem('deskbeam_theme',h.classList.contains('dark')?'dark':'light');}
if(localStorage.getItem('deskbeam_theme')==='dark')document.documentElement.classList.add('dark');
/* ── GYRO page ── */
document.getElementById('st').addEventListener('click',_openPanel);
function _gkey(t,cls){var b=document.createElement('button');b.className='gk'+(cls?' '+cls:'');b.textContent=t;return b;}
function _gTap(b,key){_tapEl(b,function(){sendCmd({type:'key_press',key:key});},true);}
(function(){
  var gl=document.getElementById('gcl'),gr=document.getElementById('gcr');
  function reg(el,side){_gBtns.push({el:el,side:side});}
  var wasd=document.createElement('div');wasd.className='gyro-dpad';
  var wsB=_gkey('WA','hl');_bindHoldCombo(wsB,['w','a']);wsB.className+=' dp-ul';wasd.appendChild(wsB);
  var wB=_gkey('W','hl');_bindHoldEl(wB,{type:'key_down',key:'w'},{type:'key_up',key:'w'},true,true);wB.className+=' dp-up';wasd.appendChild(wB);
  var wdB=_gkey('WD','hl');_bindHoldCombo(wdB,['w','d']);wdB.className+=' dp-ur';wasd.appendChild(wdB);
  var aB=_gkey('A','hl');_bindHoldEl(aB,{type:'key_down',key:'a'},{type:'key_up',key:'a'},true,true);aB.className+=' dp-left';wasd.appendChild(aB);
  var sB=_gkey('S','hl');_bindHoldEl(sB,{type:'key_down',key:'s'},{type:'key_up',key:'s'},true,true);sB.className+=' dp-down';wasd.appendChild(sB);
  var dB=_gkey('D','hl');_bindHoldEl(dB,{type:'key_down',key:'d'},{type:'key_up',key:'d'},true,true);dB.className+=' dp-right';wasd.appendChild(dB);
  reg(wasd,'L');
  var dirs=document.createElement('div');dirs.className='gyro-dpad';
  var ul=_gkey('\u2196','hl');_bindHoldCombo(ul,['up','left']);ul.className+=' dp-ul';dirs.appendChild(ul);
  var upB=_gkey('\u2191','hl');_bindHoldEl(upB,{type:'key_down',key:'up'},{type:'key_up',key:'up'},true,true);upB.className+=' dp-up';dirs.appendChild(upB);
  var ur=_gkey('\u2197','hl');_bindHoldCombo(ur,['up','right']);ur.className+=' dp-ur';dirs.appendChild(ur);
  var lfB=_gkey('\u2190','hl');_bindHoldEl(lfB,{type:'key_down',key:'left'},{type:'key_up',key:'left'},true,true);lfB.className+=' dp-left';dirs.appendChild(lfB);
  var dnB=_gkey('\u2193','hl');_bindHoldEl(dnB,{type:'key_down',key:'down'},{type:'key_up',key:'down'},true,true);dnB.className+=' dp-down';dirs.appendChild(dnB);
  var rtB=_gkey('\u2192','hl');_bindHoldEl(rtB,{type:'key_down',key:'right'},{type:'key_up',key:'right'},true,true);rtB.className+=' dp-right';dirs.appendChild(rtB);
  reg(dirs,'R');
  [['H','h',''],['END','end','']].forEach(function(k){var b=_gkey(k[0],k[2]);_gTap(b,k[1]);reg(b,'L');});
  var fR=_gkey('R','hr');_bindHoldEl(fR,{type:'mouse_right_down'},{type:'mouse_right_up'},true,true);reg(fR,'L');
  var mB=_gkey('M','blk');_bindHoldEl(mB,{type:'mouse_middle_down'},{type:'mouse_middle_up'},true,true);reg(mB,'L');
  var pu=_gkey('\u25B2');_bindHoldEl(pu,{type:'scroll_up'},null,true);reg(pu,'L');
  var pd=_gkey('\u25BC');_bindHoldEl(pd,{type:'scroll_down'},null,true);reg(pd,'L');
  [['ESC','esc',''],['Z','z','']].forEach(function(k){var b=_gkey(k[0],k[2]);_gTap(b,k[1]);reg(b,'L');});
  [['L','l',''],['M','m',''],['Q','q',''],['B','b','']].forEach(function(k){var b=_gkey(k[0],k[2]);_gTap(b,k[1]);reg(b,'L');});
  [['R','r',''],['E','e',''],['F','f','']].forEach(function(k){var b=_gkey(k[0],k[2]);_gTap(b,k[1]);reg(b,'R');});
  var sp=_gkey('SPACE','hl');_gTap(sp,'space');reg(sp,'R');
  var l=_gkey('CTRL','hl');_bindHoldEl(l,{type:'key_down',key:'ctrl'},{type:'key_up',key:'ctrl'},true,true);reg(l,'R');
  var sh=_gkey('SHIFT');reg(sh,'R');
  (function(){
    var shT=null;
    _gShSt=false;
    _gShRel=function(){
      if(_gShSt||shT){_gShSt=false;if(shT){clearTimeout(shT);shT=null;}sh.classList.remove('sh-on');sendCmd({type:'key_up',key:'shift'});}
    };
    var d=function(){
      if(_gShSt){_gShRel();return;}
      sendCmd({type:'key_down',key:'shift'});
      shT=setTimeout(function(){shT=null;_gShSt=true;sh.classList.add('sh-on');},500);
    };
    var u=function(){
      if(shT){clearTimeout(shT);shT=null;sendCmd({type:'key_up',key:'shift'});return;}
      if(_gShSt)sh.classList.add('sh-on');
    };
    sh.addEventListener('touchstart',function(e){e.preventDefault();d();});
    sh.addEventListener('touchend',function(e){e.preventDefault();u();});
    sh.addEventListener('mousedown',d);
    sh.addEventListener('mouseup',u);
    sh.addEventListener('mouseleave',u);
  })();
  [['C','c',''],['X','x','']].forEach(function(k){var b=_gkey(k[0],k[2]);_gTap(b,k[1]);reg(b,'R');});
  var tapB=_gkey('TAP');_bindHoldEl(tapB,{type:'key_down',key:'tab'},{type:'key_up',key:'tab'},true,true);reg(tapB,'R');
  [['V','v',''],['\u232b','backspace',''],['ENTER','enter','enter-key']].forEach(function(k){var b=_gkey(k[0],k[2]);_gTap(b,k[1]);reg(b,'R');});
  _gLayout();
})();
function _gLayout(){
  if(_gtt===1){sendCmd({type:'mouse_up'});_gtt=0;}
  _gHoldUp.forEach(function(h){
    if(h.el.classList.contains('pressed')){h.el.classList.remove('pressed');sendCmd(h.up);}
  });
  var mc=document.getElementById('gmc'),gl=document.getElementById('gcl'),gr=document.getElementById('gcr');
  if(!mc||!gl||!gr)return;
  var ls=window.matchMedia&&window.matchMedia('(orientation:landscape)').matches;
  _gBtns.forEach(function(b){
    if(ls){if(b.side==='L')gl.appendChild(b.el);else gr.appendChild(b.el);}
    else mc.appendChild(b.el);
  });
}
function _gySetView(on){
  document.getElementById('gyroPage').classList.toggle('novid',!on);
}
document.getElementById('gcb').addEventListener('click',function(){
  var b=this;b.classList.toggle('on');
  if(b.classList.contains('on')){if(!_so)sm(true);}
  else{if(_so)sm(false);}
  _gyShowCam(b.classList.contains('on'));
  _gySetView(b.classList.contains('on'));
});
document.getElementById('gex').addEventListener('click',_openPanel);
document.getElementById('gyroStatus').addEventListener('click',_gyToggleSensor);
document.getElementById('ggb').addEventListener('click',function(){_gSetAcc(!_gAcc);});
document.getElementById('gcal').addEventListener('click',_gyCalib);
document.getElementById('gax').addEventListener('click',function(){_gAxSwap=!_gAxSwap;_gAxBtn();});
(function(){
  var sx=document.getElementById('gsx'),sy=document.getElementById('gsy');
  var vx=document.getElementById('gsxv'),vy=document.getElementById('gsyv');
  sx.value=_gsX;sy.value=_gsY;
  if(vx)vx.textContent=_gsX;if(vy)vy.textContent=_gsY;
  sx.addEventListener('input',function(){_gsX=parseInt(this.value)||20;if(vx)vx.textContent=_gsX;});
  sy.addEventListener('input',function(){_gsY=parseInt(this.value)||20;if(vy)vy.textContent=_gsY;});
})();
/* ── GYRO touch canvas: 1-finger left-hold + virtual joystick move ── */
var _gtt=0; /* 0=none 1=left-down */
(function(){
  var ta=document.getElementById('gta');
  if(!ta)return;
  var stick=document.getElementById('gstick');
  var stickKnob=document.getElementById('gstickKnob');
  var sCX=0,sCY=0,sLx=0,sLy=0,sActive=false,sTouchId=-1;
  function _stickArea(x,y){
    if(!stick)return false;
    var r=stick.getBoundingClientRect();
    var pad=24;
    return x>=r.left-pad&&x<=r.right+pad&&y>=r.top-pad&&y<=r.bottom+pad;
  }
  ta.addEventListener('touchstart',function(e){
    var ct=e.changedTouches;
    for(var i=0;i<ct.length;i++){
      if(_stickArea(ct[i].clientX,ct[i].clientY)){
        sActive=true;sTouchId=ct[i].identifier;
        sCX=ct[i].clientX;sCY=ct[i].clientY;
        sLx=ct[i].clientX;sLy=ct[i].clientY;
        if(stick)stick.classList.add('active');
        return;
      }
    }
    var n=e.targetTouches.length-(sActive?1:0);
    if(n===1&&_gtt===0){sendCmd({type:'mouse_down'});_gtt=1;}
    else if(n>1&&_gtt===1){sendCmd({type:'mouse_up'});_gtt=0;}
  },{passive:true});
  ta.addEventListener('touchmove',function(e){
    if(sActive){
      var t;for(var i=0;i<e.touches.length;i++){if(e.touches[i].identifier===sTouchId){t=e.touches[i];break;}}
      if(t){
        var ox=t.clientX-sCX,oy=t.clientY-sCY;
        var d=Math.hypot(ox,oy);
        if(d>20){ox=ox/d*20;oy=oy/d*20;}
        var dx=Math.round((t.clientX-sLx)*3);
        var dy=Math.round((t.clientY-sLy)*3);
        if(dx>40)dx=40;if(dx<-40)dx=-40;
        if(dy>40)dy=40;if(dy<-40)dy=-40;
        sLx=t.clientX;sLy=t.clientY;
        if(stickKnob)stickKnob.style.transform='translate('+ox+'px,'+oy+'px)';
        if(dx||dy)sendCmd({type:'mouse_move',dx:dx,dy:dy});
        return;
      }
    }
    e.preventDefault();
  },{passive:false});
  ta.addEventListener('touchend',function(e){
    for(var i=0;i<e.changedTouches.length;i++){
      if(e.changedTouches[i].identifier===sTouchId){
        sActive=false;sTouchId=-1;
        if(stick)stick.classList.remove('active');
        if(stickKnob)stickKnob.style.transform='translate(-50%,-50%)';
        return;
      }
    }
    if(!sActive&&e.targetTouches.length===0){if(_gtt===1)sendCmd({type:'mouse_up'});_gtt=0;}
  },{passive:true});
  ta.addEventListener('touchcancel',function(e){
    for(var i=0;i<e.changedTouches.length;i++){
      if(e.changedTouches[i].identifier===sTouchId){
        sActive=false;sTouchId=-1;
        if(stick)stick.classList.remove('active');
        if(stickKnob)stickKnob.style.transform='translate(-50%,-50%)';
        return;
      }
    }
    if(!sActive&&e.targetTouches.length===0){if(_gtt===1)sendCmd({type:'mouse_up'});_gtt=0;}
  },{passive:true});
})();
/* ── GYRO input row (text + voice, like main page) ── */
var gti=document.getElementById('gti'),gsn=document.getElementById('gsn'),grc=document.getElementById('grc');
if(gsn){
  gsn.addEventListener('click',function(){var t=gti.value;if(!t)return;sendCmd({type:'type_text',text:t.replace(/\n/g,' ')});gti.value='';gti.focus();});
}
if(grc){
  _bindRec(grc);
}

cn();cnV();