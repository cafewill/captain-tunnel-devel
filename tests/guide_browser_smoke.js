/* 생성 HTML 끝에 주입하여 실제 Chromium DOM과 이벤트를 검사한다. */
(function(){
 const results=[];
 function check(name,run){try{if(!run())throw new Error('assertion failed');results.push({name,ok:true});}catch(error){results.push({name,ok:false,error:String(error)});}}
 const get=id=>document.getElementById(id);
 check('rendered Markdown',()=>document.documentElement.dataset.guideReady==='true'&&document.querySelector('.content p,.content table,.content ol'));
 check('one active tab',()=>document.querySelectorAll('.section.active').length===1&&document.querySelectorAll('.tab[aria-selected=true]').length===1);
 check('tab navigation and fragment',()=>{const tabs=document.querySelectorAll('.tab');const last=tabs[tabs.length-1];last.click();return get(last.getAttribute('aria-controls')).classList.contains('active')&&location.hash==='#'+last.getAttribute('aria-controls');});
 check('open preferences',()=>{get('prefsBtn').click();return !get('prefsPanel').hidden&&get('prefsBtn').getAttribute('aria-expanded')==='true'&&document.activeElement===get('themeToggle');});
 check('night and day',()=>{const control=get('themeToggle');control.checked=true;control.dispatchEvent(new Event('change'));const dark=document.documentElement.dataset.theme==='dark';control.checked=false;control.dispatchEvent(new Event('change'));return dark&&document.documentElement.dataset.theme==='light';});
 for(const skin of ['green','spring','summer','autumn','winter'])check('skin '+skin,()=>{const radio=document.querySelector('.skin-dot[value='+skin+']');radio.click();return (document.documentElement.dataset.skin||'green')===skin&&radio.checked;});
 check('font upper bound',()=>{for(let n=0;n<20;n++)get('fontUp').click();return get('fontVal').textContent==='20'&&get('fontUp').disabled;});
 check('font lower bound',()=>{for(let n=0;n<20;n++)get('fontDown').click();return get('fontVal').textContent==='12'&&get('fontDown').disabled;});
 check('preferences saved',()=>{try{return localStorage.getItem('captain-tunnel-guide-font')==='12'&&localStorage.getItem('captain-tunnel-guide-skin')==='winter';}catch(e){return true;}});
 check('Escape closes and restores focus',()=>{document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));return get('prefsPanel').hidden&&document.activeElement===get('prefsBtn');});
 check('outside click closes preferences',()=>{get('prefsBtn').click();get('main').click();return get('prefsPanel').hidden;});
 check('reset preferences',()=>{get('resetBtn').click();return !document.documentElement.dataset.skin&&!document.documentElement.dataset.theme&&get('fontVal').textContent==='15';});
 check('print button',()=>{let called=false;window.print=()=>{called=true;};get('printBtn').click();return called;});
 check('local guide links use HTML',()=>Array.from(document.querySelectorAll('.content a')).every(a=>!/^((?!\.\.\/).)*\.md(?:#.*)?$/.test(a.getAttribute('href'))));
 check('no page horizontal overflow',()=>document.documentElement.scrollWidth<=innerWidth+1);
 const output=document.createElement('pre');output.id='guide-smoke-results';output.dataset.failed=String(results.filter(r=>!r.ok).length);output.textContent=JSON.stringify(results);document.body.append(output);
 document.documentElement.dataset.smokeComplete='true';
})();
