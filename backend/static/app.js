let token=localStorage.getItem('recovery_token');let me=JSON.parse(localStorage.getItem('recovery_user')||'null');let pendingUser=null;
const $=id=>document.getElementById(id);
async function login(){let u=$('username').value.trim(),p=$('password').value;if(!u||!p)return showToast('Enter username and password.','warning');$('loginMsg').textContent='Signing in...';$('loginMsg').className='message';try{let d=await api('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p,device_id:deviceId()})});if(!d||typeof d!=='object')throw Error('Login failed: server returned an invalid response.');if(!d.token)throw Error(d.error||d.message||'Login failed: server returned no login token.');if(!d.user)throw Error('Login failed: server returned no user profile.');token=d.token;me=d.user;localStorage.setItem('recovery_token',token);localStorage.setItem('recovery_user',JSON.stringify(me));boot()}catch(e){$('loginMsg').textContent=e&&e.message?e.message:'Login failed.';$('loginMsg').className='message error'}}
function deviceId(){let id=localStorage.getItem('recovery_device_id');if(!id){id=(crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random());localStorage.setItem('recovery_device_id',id)}return id}
async function api(url,opt={}){opt.headers=Object.assign({},opt.headers||{},token?{'Authorization':'Bearer '+token}:{});let r;try{r=await fetch('/api'+url,opt)}catch(e){throw Error('Unable to connect to server.')};let d=await r.json().catch(()=>({}));if(r.status===401){localStorage.removeItem('recovery_token');localStorage.removeItem('recovery_user');token=null;me=null;throw Error(d.error||d.message||'Authentication required')}if(!r.ok)throw Error(d.error||d.message||'Request failed');return d}
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
function formatDateTime(v){if(!v)return'—';let d=new Date(v);return isNaN(d)?esc(v):d.toLocaleString('en-IN',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'})}
function showToast(message,type='success'){let c=$('toastContainer');if(!c)return;let t=document.createElement('div');t.className='toast '+(type==='error'?'toast-error':type==='warning'?'toast-warning':'');t.textContent=message;c.appendChild(t);setTimeout(()=>{t.classList.add('toast-hide');setTimeout(()=>t.remove(),300)},3500)}
const labels={dashboard:'Dashboard',users:'User Management',banks:'Bank Management',twowheeler:'Two Wheeler Files',fourwheeler:'Four Wheeler Files',cv:'Commercial Vehicle (CV) Files',search:'Data Search',upload:'File Upload',reports:'Reports',notifications:'Notifications',profile:'My Profile'};
function boot(){if(!token||!me){if($('username'))$('username').value='';if($('password'))$('password').value='';return}$('login').classList.add('hidden');$('app').classList.remove('hidden');applyRoleUi();updateHeaderUser();show('dashboard')}
function applyRoleUi(){let staff=me&&(me.role==='ADMIN'||me.role==='STAFF'||me.role==='OFFICE_STAFF'||me.role==='OFFICE_STAFF');['users','banks','upload','reports'].forEach(id=>{let b=document.querySelector(`nav button[onclick=\"show('${id}')\"]`);if(b)b.style.display=staff?'block':'none'});let fm=document.querySelector('.nav-group');if(fm)fm.style.display=staff?'block':'none';}
function updateHeaderUser(){if(me)$('who').textContent=`${me.full_name||me.username||'User'} · ${me.role||'USER'}`}
async function logout(){try{if(token)await api('/auth/logout',{method:'POST'});}catch(e){}localStorage.removeItem('recovery_token');localStorage.removeItem('recovery_user');token=null;me=null;location.reload()}
function show(id){const protectedPages=['users','banks','upload','reports'];if(protectedPages.includes(id)&&!(me&&(me.role==='ADMIN'||me.role==='STAFF'))){showToast('This section is available only to admin/office staff.','error');return}document.querySelectorAll('.page').forEach(x=>x.classList.add('hidden'));let p=$(id);if(!p)return;p.classList.remove('hidden');$('title').textContent=labels[id]||id;let f={dashboard:loadDashboard,users:loadUsers,banks:loadBanks,twowheeler:loadFiles,fourwheeler:loadFiles,cv:loadFiles,search:()=>search(true),upload:loadBankSelect,reports:loadReports,notifications:loadNotifications,profile:loadProfile}[id];if(f)Promise.resolve(f()).catch(e=>showToast(e.message,'error'))}
function toggleFileMenu(){$('fileSubmenu').classList.toggle('open');$('fileMenuArrow').textContent=$('fileSubmenu').classList.contains('open')?'▴':'▾'}
async function loadDashboard(){let d=await api('/stats');let total=+d.total||0,two=+d.two_wheeler||0,four=+d.four_wheeler||0,cv=+d.cv||0,holds=+d.holds||0,yard=+d.in_yard||0;let vals=[two,four,cv,holds,yard],sum=vals.reduce((a,b)=>a+b,0);$('cards').innerHTML=[['Total Vehicles',total],['Two Wheeler',two],['Four Wheeler',four],['CV',cv],['Holds',holds],['In Yard',yard]].map(x=>`<div class="card"><span>${x[0]}</span><strong>${x[1].toLocaleString()}</strong></div>`).join('');$('pieTotal').textContent=total.toLocaleString();let start=0,parts=vals.map((v,i)=>{let end=start+(sum?v/sum*360:0),p=`#${['17233b','456b8c','6b7280','c2414c','2f855a'][i]}`;let x=`${p} ${start}deg ${end}deg`;start=end;return x});$('pieChart').style.background=`conic-gradient(${parts.join(',')},#dfe6ef ${start}deg 360deg)`;let names=['Two Wheeler','Four Wheeler','CV','Holds','In Yard'];$('pieLegend').innerHTML=names.map((n,i)=>`<div class="legend-item"><span><i class="legend-dot" style="background:#${['17233b','456b8c','6b7280','c2414c','2f855a'][i]}"></i>${n}</span><b>${vals[i].toLocaleString()}</b></div>`).join('');$('status').innerHTML=[['Total Records',total],['Active Holds',holds],['Vehicles In Yard',yard]].map(x=>`<div class="summary-item"><span>${x[0]}</span><b>${x[1].toLocaleString()}</b></div>`).join('')}
let searchTimer=null;
function setupWebRegistrationSearch(){
  const q=$('q'); if(!q) return;
  q.addEventListener('input',()=>{
    q.value=q.value.replace(/\D/g,'').slice(0,4);
    clearTimeout(searchTimer);
    if(q.value.length===4){
      searchTimer=setTimeout(()=>search(true),0);
    } else if(q.value.length===0){
      $('results').innerHTML='';
    }
  });
}
async function search(auto=false){
  let q=$('q').value.trim().replace(/\D/g,'').slice(0,4);
  $('q').value=q;
  if(q.length!==4){
    if(!auto) showToast('Enter exactly 4 digits.','warning');
    return;
  }
  try{renderVehicles(await api(`/vehicles/search?field=registration_no&q=${encodeURIComponent(q)}`))}
  catch(e){showToast(e.message,'error')}
}
function renderVehicles(rows){
  $('results').innerHTML=rows.length?`<div class="table-wrapper"><table><thead><tr><th>Registration</th><th>Customer</th><th>Account</th><th>Chassis</th><th>Type</th><th>Status</th><th>Action</th></tr></thead><tbody>${rows.map(v=>`<tr class="vehicle-row" onclick="showVehicleDetails(${JSON.stringify(v.id)})"><td><b>${esc(v.registration_no)}</b></td><td>${esc(v.customer_name)}</td><td>${esc(v.account_no)}</td><td>${esc(v.chassis_no)}</td><td>${esc(v.vehicle_type)}</td><td><span class="status-pill">${esc(v.status)}</span></td><td class="action-buttons" onclick="event.stopPropagation()"><button onclick="setStatus(${JSON.stringify(v.id)},'HOLD')">Hold</button><button onclick="setStatus(${JSON.stringify(v.id)},'IN_YARD')">Yard</button><button onclick="setStatus(${JSON.stringify(v.id)},'RELEASE')">Release</button>${me?.role==='ADMIN'?`<button class="danger" onclick="deleteVehicle(${JSON.stringify(v.id)})">Delete</button>`:''}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty-state">No records found.</div>'
}
async function showVehicleDetails(id){
  try{
    const v=await api(`/vehicles/${id}`);
    const message=`RecoveryOps Vehicle Details\n\nBank: ${v.bank_name||'N/A'}\nRegistration: ${v.registration_no||'N/A'}\nCustomer: ${v.customer_name||'N/A'}\nVehicle Type: ${v.vehicle_type||'N/A'}\nChassis: ${v.chassis_no||'N/A'}\nEngine: ${v.engine_no||'N/A'}\nMobile: ${v.mobile||'N/A'}\nCity: ${v.city||'N/A'}\nState: ${v.state||'N/A'}\nPincode: ${v.pincode||'N/A'}\nStatus: ${v.status||'N/A'}\nAccount: ${v.account_no||'N/A'}\nLoan: ${v.loan_no||'N/A'}`;
    const wa=`https://wa.me/?text=${encodeURIComponent(message)}`;
    const modal=document.createElement('div');
    modal.className='modal';
    modal.innerHTML=`<div class="modal-card vehicle-detail-card"><button class="modal-close" aria-label="Close">×</button><h3>${esc(v.registration_no||'Vehicle Details')}</h3><div class="vehicle-detail-body">${[['Registration Number',v.registration_no],['Customer',v.customer_name],['Vehicle Type',v.vehicle_type],['Bank',v.bank_name],['Chassis',v.chassis_no],['Engine',v.engine_no],['Mobile',v.mobile],['City',v.city],['State',v.state],['Pincode',v.pincode],['Status',v.status],['Account',v.account_no],['Loan',v.loan_no]].map(x=>`<p><b>${esc(x[0])}:</b> ${esc(x[1]||'N/A')}</p>`).join('')}</div><div class="detail-actions"><button class="confirm-btn">Confirmation Required</button><button class="whatsapp-btn hidden">Share on WhatsApp</button></div></div>`;
    document.body.appendChild(modal);
    modal.querySelector('.modal-close').onclick=()=>modal.remove();
    modal.addEventListener('click',e=>{if(e.target===modal)modal.remove()});
    modal.querySelector('.confirm-btn').onclick=()=>{
      if(!confirm('Confirm that you want to share this vehicle information on WhatsApp?')) return;
      modal.querySelector('.whatsapp-btn').classList.remove('hidden');
      window.open(wa,'_blank','noopener');
    };
    modal.querySelector('.whatsapp-btn').onclick=()=>window.open(wa,'_blank','noopener');
  }catch(e){showToast(e.message,'error')}
}
async function setStatus(id,status){try{await api(`/vehicles/${id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});showToast(`Vehicle status changed to ${status}.`);search();loadDashboard()}catch(e){showToast(e.message,'error')}}
async function deleteVehicle(id){if(!confirm('Delete this vehicle permanently?'))return;try{await api(`/vehicles/${id}`,{method:'DELETE'});showToast('Vehicle deleted.');search();loadDashboard()}catch(e){showToast(e.message,'error')}}
async function loadBankSelect(){let d=await api('/banks');$('bank').innerHTML='<option value="">Select Bank</option>'+d.map(b=>`<option value="${b.id}">${esc(b.name)}</option>`).join('')}
async function upload(){
  let fi=$('file'),bank=$('bank').value;
  if(!fi.files?.length)return showToast('Please select a CSV or Excel file.','warning');
  if(!bank)return showToast('Please select a bank.','warning');
  let file=fi.files[0], max=50*1024*1024;
  if(file.size>max){showToast('File is too large. Maximum upload size is 50 MB.','error');return}
  let fd=new FormData();fd.append('file',file);fd.append('bank_id',bank);fd.append('vehicle_type',$('vehicleType').value);
  let btn=$('uploadButton'),pr=$('uploadProgress'),bar=$('uploadProgressBar');
  btn.disabled=true;btn.textContent='⏳ Uploading...';pr.classList.remove('hidden');bar.style.width='0%';$('uploadProgressText').textContent='Uploading… 0%';
  try{
    const d=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();
      xhr.open('POST','/api/files/import',true);
      xhr.setRequestHeader('Authorization','Bearer '+token);
      xhr.timeout=15*60*1000;
      xhr.upload.onprogress=e=>{
        if(e.lengthComputable){
          const pct=Math.min(99,Math.round(e.loaded*100/e.total));
          bar.style.width=pct+'%';$('uploadProgressText').textContent=`Uploading… ${pct}%`;
        }
      };
      xhr.onload=()=>{
        let data={};try{data=JSON.parse(xhr.responseText||'{}')}catch(e){}
        if(xhr.status===413)return reject(Error('File is too large. Maximum upload size is 50 MB.'));
        if(xhr.status<200||xhr.status>=300)return reject(Error(data.error||`Upload failed (${xhr.status})`));
        resolve(data);
      };
      xhr.onerror=()=>reject(Error('Upload failed. Server connection was interrupted.'));
      xhr.ontimeout=()=>reject(Error('Upload request timed out.'));
      xhr.send(fd);
    });
    const fileId=d.file_id;
    let done=null;
    for(let i=0;i<1800;i++){
      let st;
      try{
        st=await api(`/files/${fileId}`);
      }catch(err){
        // A freshly queued job can briefly race the status read on a hosted DB.
        // Retry 404s instead of turning a successful upload into a false failure.
        if(String(err.message||'').includes('File not found') || String(err.message||'').includes('404')){
          await new Promise(r=>setTimeout(r,1000));
          continue;
        }
        throw err;
      }
      let status=(st.status||'').toUpperCase();
      let pct=st.total>0?Math.min(99,Math.round((+st.record_count||0)*100/+st.total)):0;
      if(status==='IMPORTED'){done=st;bar.style.width='100%';$('uploadProgressText').textContent='Import completed • 100%';break}
      if(status==='FAILED')throw Error(st.error||'File import failed. Check server logs for the import error.');
      bar.style.width=Math.max(1,pct)+'%';
      $('uploadProgressText').textContent=pct>0?`Importing… ${pct}%`:'Importing…';
      await new Promise(r=>setTimeout(r,1000));
    }
    if(!done)throw Error('Import is still running. Check File Management for the final status.');
    $('uploadOut').innerHTML=`<div class="upload-success"><h4>✓ Upload & Import Successful</h4><p><b>${done.created||done.record_count||0}</b> created · <b>${done.duplicates||0}</b> duplicates · <b>${done.invalid||0}</b> invalid</p><p>${esc(file.name)} · ${formatDateTime(new Date())}</p><p>Replaced files: ${done.replaced_files||0} · Replaced records: ${done.replaced_records||0}</p></div>`;
    showToast('File uploaded and imported successfully.');
  }catch(e){
    bar.style.width='0%';$('uploadProgressText').textContent='Upload/import failed';$('uploadOut').innerHTML=`<div class="message error">✕ ${esc(e.message)}</div>`;showToast(e.message,'error')
  }finally{setTimeout(()=>{btn.disabled=false;btn.textContent='Upload & Import'},500)}
}
async function loadBanks(){let d=await api('/banks');$('banksOut').innerHTML=`<div class="table-wrapper"><table><thead><tr><th>Name</th><th>Code</th><th>Phone</th><th>Email</th><th>Action</th></tr></thead><tbody>${d.map(b=>`<tr><td>${esc(b.name)}</td><td>${esc(b.code)}</td><td>${esc(b.phone)}</td><td>${esc(b.email)}</td><td class="action-buttons"><button onclick="editBank(${JSON.stringify(b.id)})">Edit</button><button class="danger" onclick="deleteBank(${JSON.stringify(b.id)})">Delete</button></td></tr>`).join('')}</tbody></table></div>`}
async function addBank(){let body={name:$('bankName').value.trim(),code:$('bankCode').value.trim(),phone:$('bankPhone').value.trim(),email:$('bankEmail').value.trim()};if(!body.name||!body.code)return showToast('Bank name and code are required.','warning');try{await api('/banks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});['bankName','bankCode','bankPhone','bankEmail'].forEach(id=>$(id).value='');showToast('Bank created.');loadBanks()}catch(e){showToast(e.message,'error')}}
async function editBank(id){let n=prompt('Bank name:');if(n===null)return;let c=prompt('Bank code:');if(c===null)return;let p=prompt('Phone:');if(p===null)return;let e=prompt('Email:');if(e===null)return;try{await api(`/banks/${id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n,code:c,phone:p,email:e})});showToast('Bank updated.');loadBanks()}catch(x){showToast(x.message,'error')}}
async function deleteBank(id){if(!confirm('Delete this bank?'))return;try{await api(`/banks/${id}`,{method:'DELETE'});showToast('Bank deleted.');loadBanks()}catch(e){showToast(e.message,'error')}}
function startCreateUser(){pendingUser={username:$('newUsername').value.trim(),full_name:$('newName').value.trim(),email:$('newEmail').value.trim(),phone:$('newPhone').value.trim(),password:$('newPassword').value,role:$('newRole').value,active:$('newStatus').value==='true'};if(!pendingUser.username||!pendingUser.full_name||!pendingUser.password||(!pendingUser.email&&!pendingUser.phone))return showToast('Fill username, name, password and email/mobile.','warning');if(pendingUser.password.length<8)return showToast('Password must be at least 8 characters.','warning');requestUserOtp()}
async function requestUserOtp(){try{let d=await api('/users/request-otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:pendingUser.email,phone:pendingUser.phone})});$('otpModal').classList.remove('hidden');$('otpInput').value='';$('otpMsg').textContent=d.otp_dev?`Development OTP: ${d.otp_dev}`:'OTP sent.';showToast('OTP generated.')}catch(e){showToast(e.message,'error')}}
function closeOtpModal(){$('otpModal').classList.add('hidden')}
async function verifyUserOtp(){let otp=$('otpInput').value.trim();if(!otp)return $('otpMsg').textContent='Enter OTP.';try{await api('/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...pendingUser,otp})});closeOtpModal();['newUsername','newName','newEmail','newPhone','newPassword'].forEach(id=>$(id).value='');showToast('User created successfully.');loadUsers()}catch(e){$('otpMsg').textContent=e.message}}
async function loadUsers(){let d=await api('/users');$('usersOut').innerHTML=`<div class="table-wrapper"><table><thead><tr><th>Username</th><th>Name</th><th>Email</th><th>Mobile</th><th>Role</th><th>Status</th><th>Device</th><th>Action</th></tr></thead><tbody>${d.map(u=>`<tr><td>${esc(u.username)}</td><td>${esc(u.full_name)}</td><td>${esc(u.email)}</td><td>${esc(u.phone)}</td><td>${esc(u.role)}</td><td><span class="status-pill ${u.active?'status-active':'status-paused'}">${u.is_deleted?'Deleted':u.active?'Active':'Paused'}</span></td><td>${u.device_id?'Linked':'Not linked'}</td><td>${u.role==='SUPER_ADMIN'?'<span>Protected</span>':`<div class="action-buttons"><button onclick="editUser(${JSON.stringify(u.id)})">Edit</button><button onclick="toggleUserStatus(${JSON.stringify(u.id)},${!u.active})">${u.active?'Pause':'Activate'}</button><button onclick="removeUserDevice(${JSON.stringify(u.id)})">Remove Device</button><button class="danger" onclick="deleteUser(${JSON.stringify(u.id)})">Delete</button></div>`}</td></tr>`).join('')}</tbody></table></div>`}
async function editUser(id){
  let u=(await api('/users')).find(x=>x.id===id);
  if(!u)return;
  let username=prompt('Username:',u.username||''); if(username===null)return;
  let name=prompt('Full name:',u.full_name||''); if(name===null)return;
  let email=prompt('Email:',u.email||''); if(email===null)return;
  let phone=prompt('Mobile:',u.phone||''); if(phone===null)return;
  let role=prompt('Role (ADMIN/REPO_AGENT/STAFF):',u.role||'REPO_AGENT'); if(role===null)return;
  let password=prompt('New password (leave blank to keep current password):','');
  if(password===null)return;
  try{
    await api(`/users/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:username.trim(),full_name:name,email,phone,role:role.trim().toUpperCase(),password})});
    showToast('User updated.'); loadUsers();
  }catch(x){showToast(x.message,'error')}
}
async function toggleUserStatus(id,active){try{await api(`/users/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({active})});showToast(active?'User activated.':'User paused.');loadUsers()}catch(e){showToast(e.message,'error')}}
async function removeUserDevice(id){if(!confirm('Remove device access for this user?'))return;try{await api(`/users/${id}/device`,{method:'DELETE'});showToast('Device access removed.');loadUsers()}catch(e){showToast(e.message,'error')}}
async function deleteUser(id){if(!confirm('Delete this user?'))return;try{await api(`/users/${id}`,{method:'DELETE'});showToast('User deleted.');loadUsers()}catch(e){showToast(e.message,'error')}}
async function loadFiles(){let d=await api('/files');let id=document.querySelector('.page:not(.hidden)')?.id;let filter=id==='twowheeler'?'TWO':id==='fourwheeler'?'FOUR':id==='cv'?'CV':null;if(filter)d=d.filter(x=>(x.vehicle_type||'').toUpperCase().includes(filter));let target=id==='twowheeler'?'twoOut':id==='fourwheeler'?'fourOut':id==='cv'?'cvOut':'filesOut';if($(target))$(target).innerHTML=tableFiles(d)}
function tableFiles(d){return d.length?`<div class="table-wrapper"><table><thead><tr><th>File</th><th>Vehicle Type</th><th>Bank</th><th>Records</th><th>Upload Date & Time</th><th>Status</th><th>Action</th></tr></thead><tbody>${d.map(x=>`<tr><td>${esc(x.filename)}</td><td>${esc(x.vehicle_type)}</td><td>${esc(x.bank_id)}</td><td>${(+x.record_count||0).toLocaleString()}</td><td>${formatDateTime(x.created_at)}</td><td>${esc(x.status)}</td><td><button class="danger" onclick="deleteFile(${JSON.stringify(x.id)})">Delete</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty-state">No files uploaded yet.</div>'}
async function deleteFile(id){if(!confirm('Delete this file and imported records?'))return;try{await api(`/files/${id}`,{method:'DELETE'});showToast('File deleted.');loadFiles();loadDashboard()}catch(e){showToast(e.message,'error')}}
function toggleExportBank(){$('exportBank').classList.toggle('hidden',$('exportScope').value!=='bank')}
async function loadReports(){let d=await api('/reports');$('reportsOut').innerHTML=d.map(x=>`<div class="notice"><div><b>${esc(x.name)}</b><p>${esc(x.report_type)} · ${esc(x.status)} · ${formatDateTime(x.created_at)}</p></div></div>`).join('')||'<div class="empty-state">No reports created.</div>';loadBankSelectForReport()}
async function loadBankSelectForReport(){let d=await api('/banks');$('exportBank').innerHTML='<option value="">Select Bank</option>'+d.map(b=>`<option value="${b.id}">${esc(b.name)}</option>`).join('')}
async function exportReport(format){let scope=$('exportScope').value,b=$('exportBank').value;if(scope==='bank'&&!b)return showToast('Select a bank first.','warning');let url=`/reports/export?format=${format}`+(scope==='bank'?`&bank_id=${encodeURIComponent(b)}`:'');try{let r=await fetch('/api'+url,{headers:{Authorization:'Bearer '+token}});if(!r.ok)throw Error((await r.json().catch(()=>({}))).error||'Export failed');let blob=await r.blob(),u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=`RecoveryOps_${scope==='bank'?'bank':'all'}_data.${format==='xlsx'?'xlsx':'csv'}`;a.click();URL.revokeObjectURL(u);showToast('Report exported successfully.')}catch(e){showToast(e.message,'error')}}
async function createReport(){try{await api('/reports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('reportName').value,report_type:$('reportType').value})});showToast('Report created.');loadReports()}catch(e){showToast(e.message,'error')}}
async function loadNotifications(){let d=await api('/notifications');let unread=d.filter(x=>!x.read).length;$('notificationBadge').textContent=unread>99?'99+':unread;$('notificationBadge').classList.toggle('hidden',!unread);$('notificationsOut').innerHTML=d.map(x=>`<div class="notice"><div>🔔</div><div><b>${esc(x.title)}</b><p>${esc(x.message)}</p><small>${formatDateTime(x.created_at)}</small></div></div>`).join('')||'<div class="empty-state">No notifications.</div>'}
async function loadProfile(){let d=await api('/users/me');me=d.user||d;localStorage.setItem('recovery_user',JSON.stringify(me));$('profileName').textContent=me.full_name||me.username;$('profileAvatar').textContent=(me.full_name||me.username||'A')[0].toUpperCase();$('profileRole').textContent=me.role;$('profileUsername').value=me.username||'';$('profileFullName').value=me.full_name||'';$('profileEmail').value=me.email||'';$('profilePhone').value=me.phone||'';$('profileRoleInput').value=me.role||'';$('profileStatus').textContent=me.active?'Active':'Paused';$('profileStatus').className='status-badge '+(me.active?'active':'paused');$('currentDevice').textContent=me.device_id?'Device linked':'Not linked';updateHeaderUser()}
async function updateProfile(){
  try{
    let d=await api('/users/me',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      username:$('profileUsername').value.trim(),
      full_name:$('profileFullName').value.trim(),
      email:$('profileEmail').value.trim(),
      phone:$('profilePhone').value.trim(),
      password:$('profilePassword').value
    })});
    me=d.user||me; localStorage.setItem('recovery_user',JSON.stringify(me));
    $('profilePassword').value='';
    $('profileMsg').textContent='Profile updated successfully.';
    showToast('Profile updated.'); updateHeaderUser();
  }catch(e){$('profileMsg').textContent=e.message;$('profileMsg').className='message error'}
}
async function removeMyDeviceAccess(){if(!confirm('Remove device access? This will log this account out immediately.'))return;try{await api('/users/me/device',{method:'DELETE'});localStorage.removeItem('recovery_token');localStorage.removeItem('recovery_user');token=null;me=null;showToast('Device removed. Logging out...');setTimeout(()=>location.reload(),250)}catch(e){showToast(e.message,'error')}}
function voiceSearch(){let SR=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SR)return showToast('Voice search is not supported.','warning');let r=new SR();r.lang='hi-IN';r.onresult=e=>{let t=e.results[0][0].transcript;$('voiceStatus').textContent='Heard: '+t;let d=spokenDigit(t);if(d!==null){$('field').value='registration_no';$('q').value=d;searchSuffix(d)}else{$('q').value=t;search()}};r.start();$('voiceStatus').textContent='🎙 Listening…'}
function spokenDigit(t){t=(t||'').toLowerCase();let m={'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'शून्य':0,'एक':1,'दो':2,'तीन':3,'चार':4,'पाँच':5,'पांच':5,'छह':6,'सात':7,'आठ':8,'नौ':9,'௦':0,'௧':1,'௨':2,'௩':3,'௪':4,'௫':5,'௬':6,'௭':7,'௮':8,'௯':9};if(/^[0-9]$/.test(t.trim()))return +t.trim();for(let k in m)if(t.includes(k))return m[k];return null}
async function searchSuffix(d){try{renderVehicles(await api(`/search/registration-suffix?suffix=${encodeURIComponent(d)}`))}catch(e){showToast(e.message,'error')}}
boot();

if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',setupWebRegistrationSearch); else setupWebRegistrationSearch();
