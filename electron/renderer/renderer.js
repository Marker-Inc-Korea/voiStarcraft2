"use strict";
const statusNode=document.getElementById("status"),feedback=document.getElementById("feedback"),result=document.getElementById("result"),lifecycle=document.getElementById("lifecycle"),eventsNode=document.getElementById("events"),registryNode=document.getElementById("registry");
async function refreshStatus(){try{const value=await window.commander.status();statusNode.textContent="Commander 응답: "+(value.status||"수신됨 (게임 상태 확인 필요)");statusNode.title=JSON.stringify(value); }catch(error){statusNode.textContent="Commander 연결 실패";}}
let historyCursor=0;
async function refreshRegistry(){try{registryNode.textContent=JSON.stringify(await window.commander.registry(),null,2);}catch(error){registryNode.textContent=`registry 오류: ${error.message}`;}}
async function refreshEvents(){try{const value=await window.commander.events(historyCursor);const items=Array.isArray(value.events)?value.events:[];if(items.length){historyCursor=Math.max(historyCursor,...items.map(item=>Number(item.seq||item.sequence||item.id||0)).filter(Number.isFinite));eventsNode.textContent=JSON.stringify(items.slice(-20),null,2);const latest=items[items.length-1];const text=latest?.clarification_prompt||latest?.message||latest?.status||latest?.event_type||"이벤트 수신";lifecycle.textContent=text;}}catch(error){lifecycle.textContent=`이벤트 오류: ${error.message}`;}}
document.getElementById("command-form").addEventListener("submit",async event=>{event.preventDefault();const input=document.getElementById("command"),text=input.value.trim();if(!text)return;feedback.textContent="명령을 해석하고 있습니다…";try{const value=await window.commander.sendCommand(text);result.textContent=JSON.stringify(value,null,2);feedback.textContent="요청 전달됨 — 게임 완료 여부는 실행 상태로 확인합니다.";input.value="";}catch(error){feedback.textContent=`전달 실패: ${error.message}`;}});
let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try { await refreshStatus(); } finally { polling = false; }
}
poll(); setInterval(poll, 2000);
document.getElementById("pin-form").addEventListener("submit",async event=>{event.preventDefault();const name=document.getElementById("pin-name").value.trim(),target=document.getElementById("pin-target").value.trim(),pinFeedback=document.getElementById("pin-feedback");try{await window.commander.registerTargetPin({name,target,metadata:{source:"electron"}});pinFeedback.textContent="semantic target pin 저장됨";await refreshRegistry();}catch(error){pinFeedback.textContent=`저장 실패: ${error.message}`;}});
setInterval(refreshEvents,2000);refreshRegistry();refreshEvents();
