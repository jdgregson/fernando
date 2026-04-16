// Persistence layer for fake-indexeddb: saves/restores IDB data to localStorage.
// Must run AFTER fake-idb-bundle.js installs window.indexedDB and BEFORE SilverBullet boots.
(function(){
  'use strict';
  var PREFIX='_fidb_';
  var DEBOUNCE=2000;
  var saveTimers={};

  function lsKey(n){return PREFIX+n}

  // --- SAVE: extract all data from a DB and write to localStorage ---
  function saveDB(db){
    var names=[];
    for(var i=0;i<db.objectStoreNames.length;i++)names.push(db.objectStoreNames[i]);
    if(!names.length)return;
    var tx;
    try{tx=db.transaction(names,'readonly')}catch(e){return}
    var data={v:db.version,stores:{}};
    var pending=names.length;
    names.forEach(function(name){
      var store=tx.objectStore(name);
      var info={kp:store.keyPath,ai:store.autoIncrement,r:[],ix:{}};
      for(var j=0;j<store.indexNames.length;j++){
        var iname=store.indexNames[j],idx=store.index(iname);
        info.ix[iname]={kp:idx.keyPath,u:idx.unique,me:idx.multiEntry};
      }
      var vr=store.getAll(),kr=store.getAllKeys(),vals=null,keys=null;
      function fin(){
        if(vals===null||keys===null)return;
        for(var x=0;x<keys.length;x++)info.r.push({k:keys[x],v:vals[x]});
        data.stores[name]=info;
        if(--pending===0){
          try{localStorage.setItem(lsKey(db.name),JSON.stringify(data))}
          catch(e){console.warn('[idb-persist] save failed:',e)}
        }
      }
      vr.onsuccess=function(){vals=vr.result;fin()};
      kr.onsuccess=function(){keys=kr.result;fin()};
    });
  }

  function scheduleSave(db){
    var n=db.name;
    if(saveTimers[n])clearTimeout(saveTimers[n]);
    saveTimers[n]=setTimeout(function(){delete saveTimers[n];saveDB(db)},DEBOUNCE);
  }

  // --- HOOK: monkey-patch IDBDatabase.prototype.transaction to intercept writes ---
  var origTx=IDBDatabase.prototype.transaction;
  IDBDatabase.prototype.transaction=function(stores,mode,opts){
    var tx=origTx.call(this,stores,mode,opts);
    if(mode==='readwrite'){
      var db=this;
      tx.addEventListener('complete',function(){scheduleSave(db)});
    }
    return tx;
  };

  // Helper: insert records into object stores, respecting keyPath
  function insertRecords(tx,storeNames,data){
    storeNames.forEach(function(sname){
      try{
        var store=tx.objectStore(sname);
        var info=data.stores[sname];
        if(!info||!info.r)return;
        var hasKeyPath=info.kp!==undefined&&info.kp!==null;
        info.r.forEach(function(r){
          try{
            // If store has a keyPath, key is in the value — don't pass explicit key
            if(hasKeyPath){store.put(r.v)}
            else{store.put(r.v,r.k)}
          }catch(e){}
        });
      }catch(e){}
    });
  }

  // --- RESTORE: pre-populate fake-indexeddb from localStorage before SB boots ---
  var keys=[];
  for(var i=0;i<localStorage.length;i++){
    var k=localStorage.key(i);
    if(k&&k.indexOf(PREFIX)===0)keys.push(k);
  }
  keys.forEach(function(key){
    var dbName=key.slice(PREFIX.length);
    var raw;
    try{raw=localStorage.getItem(key)}catch(e){return}
    if(!raw)return;
    var data;
    try{data=JSON.parse(raw)}catch(e){return}
    if(!data||!data.stores||!data.v)return;

    var upgraded=false;
    var req=indexedDB.open(dbName,data.v);
    req.onupgradeneeded=function(){
      upgraded=true;
      var db=req.result;
      var storeNames=Object.keys(data.stores);
      storeNames.forEach(function(sname){
        if(db.objectStoreNames.contains(sname))return;
        var info=data.stores[sname];
        var opts={};
        if(info.kp!==undefined&&info.kp!==null)opts.keyPath=info.kp;
        if(info.ai)opts.autoIncrement=true;
        var store=db.createObjectStore(sname,opts);
        if(info.ix){
          Object.keys(info.ix).forEach(function(iname){
            var d=info.ix[iname];
            store.createIndex(iname,d.kp,{unique:d.u,multiEntry:d.me});
          });
        }
      });
      // Insert records in the versionchange transaction
      insertRecords(req.transaction,storeNames,data);
    };
    req.onsuccess=function(){
      var db=req.result;
      if(upgraded){db.close();return}
      // No upgradeneeded — same version. Check if empty, populate if so.
      var storeNames=Object.keys(data.stores);
      var writable=storeNames.filter(function(n){return db.objectStoreNames.contains(n)});
      if(!writable.length){db.close();return}
      var chk=origTx.call(db,writable,'readonly');
      var cr=chk.objectStore(writable[0]).count();
      cr.onsuccess=function(){
        if(cr.result>0){db.close();return}
        var tx=origTx.call(db,writable,'readwrite');
        insertRecords(tx,writable,data);
        tx.oncomplete=function(){db.close()};
        tx.onerror=function(){db.close()};
      };
    };
    req.onerror=function(){};
  });
  console.log('[idb-persist] localStorage persistence layer installed');
})();
