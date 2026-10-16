"use strict";
const statusNode=document.getElementById("status"),feedback=document.getElementById("feedback"),result=document.getElementById("result");
async function refreshStatus(){try{const value=await window.commander.status();statusNode.textContent=value.status||"연결됨";statusNode.style.color="#7ae582";}catch(error){statusNode.textContent="Commander 연결 실패";statusNode.style.color="#ff6b5f";}}
document.getElementById("command-form").addEventListener("submit",async event=>{event.preventDefault();const input=document.getElementById("command"),text=input.value.trim();if(!text)return;feedback.textContent="명령을 해석하고 있습니다…";try{const value=await window.commander.sendCommand(text);result.textContent=JSON.stringify(value,null,2);feedback.textContent="요청 전달됨 — 게임 완료 여부는 실행 상태로 확인합니다.";input.value="";}catch(error){feedback.textContent=`전달 실패: ${error.message}`;}});
let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try { await refreshStatus(); } finally { polling = false; }
}
poll(); setInterval(poll, 2000);
