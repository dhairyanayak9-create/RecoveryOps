(function(){
  const $=id=>document.getElementById(id);
  const toast=(m,t)=>typeof showToast==='function'?showToast(m,t):alert(m);

  window.createSuperAgency=async function(){
    try{
      const body={
        agency_name:$('saNewAgencyName')?.value||'',
        slug:$('saNewAgencySlug')?.value||'',
        phone:$('saNewAgencyPhone')?.value||'',
        email:$('saNewAgencyEmail')?.value||'',
        address:$('saNewAgencyAddress')?.value||'',
        admin_name:$('saNewAdminName')?.value||'',
        admin_email:$('saNewAdminEmail')?.value||'',
        admin_phone:$('saNewAdminPhone')?.value||'',
        password:$('saNewAdminPassword')?.value||''
      };
      await api('/super-admin/agencies/provisioned',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      toast('Agency + isolated database created.');
      ['saNewAgencyName','saNewAgencySlug','saNewAgencyPhone','saNewAgencyEmail','saNewAgencyAddress','saNewAdminName','saNewAdminEmail','saNewAdminPhone','saNewAdminPassword'].forEach(id=>{const e=$(id);if(e)e.value=''});
      if(typeof openSection==='function')openSection('dashboard');
    }catch(e){toast(e.message,'error')}
  };

  window.saUpload2=async function(){
    const f=$('saUpload2')?.files?.[0], bank=$('saUploadBank')?.value, type=$('saUploadType')?.value;
    if(!f)return toast('Select a file.','warning');
    if(!bank)return toast('Select a bank from this agency.','warning');
    if(!type)return toast('Select vehicle type.','warning');
    const fd=new FormData();fd.append('file',f);fd.append('bank_id',bank);fd.append('vehicle_type',type);
    try{
      const r=await fetch(`/api/super-admin/agencies/${window.__saSelectedAgency||document.querySelector('#saAgencySelect')?.value}/upload-v2`,{method:'POST',headers:{Authorization:'Bearer '+token},body:fd});
      const d=await r.json();if(!r.ok)throw Error(d.error||'Upload failed');
      const m=$('saUploadMsg');if(m)m.innerHTML=`<div class="message success">✓ ${Number(d.rows_created||0).toLocaleString()} records uploaded.</div>`;
      toast('Agency file uploaded.');
      if(typeof openSection==='function')openSection('files');
    }catch(e){const m=$('saUploadMsg');if(m)m.innerHTML=`<div class="message error">${esc(e.message)}</div>`;toast(e.message,'error')}
  };

  function installDeleteButtons(){
    const box=$('saAgencies');if(!box)return;
    box.querySelectorAll('tbody tr').forEach(row=>{
      if(row.dataset.deleteAdded==='1')return;
      const open=row.querySelector('button[onclick*="saSelectAgency"]');
      if(!open)return;
      const m=String(open.getAttribute('onclick')||'').match(/saSelectAgency\((\d+)/);
      if(!m)return;
      const id=Number(m[1]);
      const cell=row.lastElementChild;if(!cell)return;
      const btn=document.createElement('button');
      btn.className='danger';btn.textContent='Delete';btn.style.marginLeft='6px';
      btn.onclick=()=>hardDeleteAgency(id,row.querySelector('td')?.innerText?.split('\n')[0]||'this agency');
      cell.appendChild(btn);row.dataset.deleteAdded='1';
    });
  }

  async function hardDeleteAgency(id,name){
    if(!confirm(`PERMANENT DELETE\n\n${name}\n\nAgency, users, banks, vehicles, files and all agency database data will be permanently deleted.\n\nContinue?`))return;
    try{
      await api(`/api/super-admin/agencies/${id}/hard-delete`,{method:'DELETE'});
      toast('Agency and all agency data permanently deleted.');
      const detail=$('saDetail');if(detail)detail.classList.add('hidden');
      if(typeof openSection==='function')openSection('dashboard');
    }catch(e){toast(e.message,'error')}
  }
  window.hardDeleteAgency=hardDeleteAgency;

  function boot(){
    if(typeof me==='undefined'||!me||me.role!=='SUPER_ADMIN')return;
    const select=$('saAgencySelect');
    if(select)select.addEventListener('change',()=>{window.__saSelectedAgency=select.value?Number(select.value):null});
    const observer=new MutationObserver(installDeleteButtons);
    const box=$('saAgencies');if(box)observer.observe(box,{childList:true,subtree:true});
    installDeleteButtons();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(boot,100));else setTimeout(boot,100);
})();
