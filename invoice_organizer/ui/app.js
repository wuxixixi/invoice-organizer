/* 单页前端交互：仅调用 window.pywebview.api.*，所有响应统一 {code,data,message}。
   无任何外部网络资源；看不清/缺失字段一律显示“待人工确认”。 */
(function () {
  'use strict';

  var api = (window.pywebview && window.pywebview.api) || {};
  var currentBatchId = '';

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function toast(msg, kind) {
    var el = $('toast');
    el.textContent = msg || '';
    el.className = 'toast show' + (kind ? ' ' + kind : '');
    setTimeout(function () { el.className = 'toast'; }, 3200);
  }
  function handle(resp) {
    if (!resp || typeof resp.code === 'undefined') {
      toast('桥接层无响应，请确认桌面窗口已就绪', 'err');
      return null;
    }
    if (resp.code !== 0) {
      toast(resp.message || '操作失败', 'err');
      return null;
    }
    return resp.data;
  }
  function setMsg(id, text, kind) {
    var el = $(id);
    el.textContent = text || '';
    el.className = 'msg' + (kind ? ' ' + kind : '');
  }
  function call(name) {
    if (!api[name]) { toast('桥接接口 ' + name + ' 不可用', 'err'); return Promise.resolve(null); }
    var args = Array.prototype.slice.call(arguments, 1);
    try {
      return Promise.resolve(api[name].apply(null, args)).then(handle).catch(function (e) {
        toast('调用失败：' + (e && e.message ? e.message : e), 'err');
        return null;
      });
    } catch (e) {
      toast('调用异常：' + e.message, 'err');
      return Promise.resolve(null);
    }
  }

  // ---- 导航 ----
  Array.prototype.forEach.call(document.querySelectorAll('.nav-btn'), function (btn) {
    btn.addEventListener('click', function () {
      Array.prototype.forEach.call(document.querySelectorAll('.nav-btn'), function (b) {
        b.classList.remove('active');
      });
      Array.prototype.forEach.call(document.querySelectorAll('.view'), function (v) {
        v.classList.remove('active');
      });
      btn.classList.add('active');
      $('view-' + btn.dataset.view).classList.add('active');
    });
  });

  // ---- 视图1：设置 ----
  $('loadConfigBtn').addEventListener('click', function () {
    var pid = $('profileId').value || 'default';
    call('get_config', pid).then(function (data) {
      if (data) {
        $('configJson').value = JSON.stringify(data, null, 2);
        setMsg('configMsg', '配置已加载（version=' + data.version + '）', 'ok');
      }
    });
  });
  $('saveConfigBtn').addEventListener('click', function () {
    call('save_config', $('configJson').value).then(function (data) {
      if (data) setMsg('configMsg', '已保存，版本 ' + data.version, 'ok');
    });
  });
  $('roleSelect').addEventListener('change', function () {
    call('set_role', this.value).then(function (data) {
      if (data) toast('本地角色已切换为 ' + data.role, 'ok');
    });
  });

  // ---- 视图2：导入 ----
  $('startBtn').addEventListener('click', function () {
    call('start_watching', $('profileId').value || 'default').then(function (data) {
      if (!data) return;
      currentBatchId = data.batchId;
      $('batchBadge').textContent = data.batchId;
      $('previewBatchId').value = data.batchId;
      $('reviewBatchId').value = data.batchId;
      $('watchInfo').innerHTML = '监控已启动。<br>批次：<b>' + esc(data.batchId) +
        '</b><br>请投放文件后到「预览与确认」加载计划。';
      toast('监控已启动', 'ok');
    });
  });

  // ---- 视图3：预览确认 ----
  function renderPlan(plan) {
    var box = $('planList');
    if (!plan.items || !plan.items.length) {
      box.innerHTML = '<div class="item">暂无计划项，请先投放文件并等待写稳。</div>';
      return;
    }
    box.innerHTML = plan.items.map(function (it, i) {
      var conflict = it.conflict ? '<span class="tag conflict">冲突：' +
        esc(it.conflict.type) + '</span>' : '';
      var missing = (it.missingFields && it.missingFields.length)
        ? '<span class="tag review">待复核：' + esc(it.missingFields.join('、')) + '</span>' : '';
      return '<div class="item">' +
        '<div class="item-title">#' + (i + 1) + ' ' + esc(it.invoiceId) + ' ' +
          '<span class="tag">' + esc(it.categoryLabel || '未分类') + '</span>' + conflict + missing + '</div>' +
        '<div class="item-line">来源：<span class="path-arrow">' + esc(it.oldPath) + '</span></div>' +
        '<div class="item-line">归档：<span class="path-arrow">' + esc(it.newPath) + '</span></div>' +
        '<div class="item-line">命中规则：' + esc(it.hitRuleId || '未命中') +
          ' · 确认状态：' + (it.confirmed ? '已确认' : '待确认') + '</div>' +
        '</div>';
    }).join('');
  }
  $('loadPlanBtn').addEventListener('click', function () {
    var bid = $('previewBatchId').value || currentBatchId;
    if (!bid) { toast('请先填写批次 ID 或启动监控', 'err'); return; }
    call('get_plan', bid).then(renderPlan);
  });
  $('confirmBtn').addEventListener('click', function () {
    var bid = $('previewBatchId').value || currentBatchId;
    call('get_plan', bid).then(function (plan) {
      if (!plan) return;
      call('confirm_plan', plan.id, []).then(function (data) {
        if (data) { toast('计划已确认', 'ok'); renderPlan(plan); }
      });
    });
  });

  // ---- 视图4：批次历史 ----
  function renderBatches(batches) {
    var box = $('batchList');
    if (!batches || !batches.length) { box.innerHTML = '<div class="item">暂无批次。</div>'; return; }
    box.innerHTML = batches.map(function (b) {
      var s = b.summary || {};
      return '<div class="item">' +
        '<div class="item-title">' + esc(b.id) + ' <span class="tag">' + esc(b.status) + '</span></div>' +
        '<div class="item-line">启动：' + esc(b.startedAt) + ' · 配置版本：' + esc(b.profileVersion) + '</div>' +
        '<div class="item-line">输入 ' + esc(s.inputCount) + ' · 成功 ' + esc(s.successCount) +
          ' · 失败 ' + esc(s.failedCount) + ' · 冲突 ' + esc(s.conflictCount) +
          ' · 待复核 ' + esc(s.reviewCount) + '</div>' +
        '</div>';
    }).join('');
  }
  $('refreshBatchesBtn').addEventListener('click', function () {
    call('list_batches').then(renderBatches);
  });

  // ---- 视图5：待复核与导出 ----
  function renderReview(items) {
    var box = $('reviewList');
    if (!items || !items.length) { box.innerHTML = '<div class="item">暂无待复核项。</div>'; return; }
    box.innerHTML = items.map(function (r) {
      return '<div class="item">' +
        '<div class="item-title">' + esc(r.field_key) + ' <span class="tag review">' +
          esc(r.reason) + '</span></div>' +
        '<div class="item-line">发票：' + esc(r.invoice_id) + ' · 详情：' + esc(r.detail) + '</div>' +
        '<div class="field-edit"><label>人工回填</label>' +
          '<input id="fix_' + esc(r.id) + '" value="' + esc(r.resolution || '') + '">' +
          '<button class="btn" data-rid="' + esc(r.id) + '" data-iid="' + esc(r.invoice_id) +
            '" data-fk="' + esc(r.field_key) + '">保存</button></div>' +
        '</div>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('button[data-rid]'), function (btn) {
      btn.addEventListener('click', function () {
        var val = $('fix_' + btn.dataset.rid).value;
        call('save_review', {
          invoiceId: btn.dataset.iid, fieldKey: btn.dataset.fk,
          value: val, operator: 'operator'
        }).then(function (data) {
          if (data) toast('已回填', 'ok');
        });
      });
    });
  }
  $('loadReviewBtn').addEventListener('click', function () {
    var bid = $('reviewBatchId').value || currentBatchId;
    call('get_review', bid).then(renderReview);
  });
  $('execBtn').addEventListener('click', function () {
    var bid = $('reviewBatchId').value || currentBatchId;
    call('execute_batch', bid).then(function (data) {
      if (!data) return;
      var s = data.summary || {};
      setMsg('exportMsg', '归档完成：成功 ' + s.successCount + '，失败 ' + s.failedCount, 'ok');
      toast('批次执行完成', 'ok');
    });
  });
  $('exportBtn').addEventListener('click', function () {
    var bid = $('reviewBatchId').value || currentBatchId;
    call('export', bid, 'xlsx').then(function (data) {
      if (data) setMsg('exportMsg', '导出路径：' + data.path, 'ok');
    });
  });

  // ---- 初始加载 ----
  window.addEventListener('pywebviewready', function () {
    api = (window.pywebview && window.pywebview.api) || {};
    $('loadConfigBtn').click();
    $('refreshBatchesBtn').click();
  });
})();
