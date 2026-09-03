(function(){
  function install(){
    if(typeof me==='undefined'||!me||!['ADMIN','STAFF','SUPER_ADMIN'].includes(me.role))return;
    if(typeof tableFiles==='function'){
      window.tableFiles=function(d){
        return d.length?`<div class="table-wrapper"><table><thead><tr><th>File</th><th>Vehicle Type</th><th>Bank</th><th>Records</th><th>Upload Date & Time</th><th>Status</th><th>Action</th></tr></thead><tbody>${d.map(x=>`<tr><td>${esc(x.filename)}</td><td>${esc(x.vehicle_type||'—')}</td><td>${esc(x.bank_name||'—')}</td><td>${(+x.record_count||0).toLocaleString()}</td><td>${formatDateTime(x.created_at)}</td><td>${esc(x.status||'—')}</td><td><button class="danger" onclick="deleteFile(${JSON.stringify(x.id)})">Delete</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty-state">No files uploaded yet.</div>';
      };
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();