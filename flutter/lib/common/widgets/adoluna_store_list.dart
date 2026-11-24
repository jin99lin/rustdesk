// Adoluna Mac address-book list (b112). Grouping rules copied from iOS v3
// (ios-b112 commit e2d8f73). This file lives only on the desktop branch;
// do not edit technician_devices_page.dart here.

import 'dart:async';
import 'dart:convert';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter_hbb/common.dart';
import 'package:flutter_hbb/common/formatter/id_formatter.dart';
import 'package:flutter_hbb/common/widgets/peers_view.dart';
import 'package:flutter_hbb/models/peer_model.dart';
import 'package:flutter_hbb/models/platform_model.dart';
import 'package:get/get.dart';

const _kUngroupedStore = '__ungrouped__';
const _kOpenMerchants = 'adoluna-open-merchants';
const _kOpenStores = 'adoluna-open-stores';
const _kQueryOnlines = 'callback_query_onlines';
const _kOnlineHandler = 'adoluna_store_list_onlines';
const _kRed = Color(0xFFFF2400);

String _labelStore(String name) =>
    name == _kUngroupedStore ? translate('adoluna-ungrouped') : name;

class AdolunaStoreList extends StatefulWidget {
  final EdgeInsets? menuPadding;
  const AdolunaStoreList({Key? key, this.menuPadding}) : super(key: key);

  @override
  State<AdolunaStoreList> createState() => _AdolunaStoreListState();
}

class _AdolunaStoreListState extends State<AdolunaStoreList>
    with WidgetsBindingObserver {
  Set<String> _openMerchants = {};
  Set<String> _openStores = {};
  Timer? _onlineTimer;
  bool _onlineLoopStarted = false;
  bool _appActive = true;
  DateTime _lastOnlineQuery = DateTime.fromMillisecondsSinceEpoch(0);
  Duration _onlineInterval = const Duration(seconds: 6);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadExpandState();
    platformFFI.registerEventHandler(_kQueryOnlines, _kOnlineHandler,
        (evt) async {
      _applyOnlines(evt);
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _ensureOnlineLoop();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _onlineTimer?.cancel();
    platformFFI.unregisterEventHandler(_kQueryOnlines, _kOnlineHandler);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _appActive = state == AppLifecycleState.resumed ||
        state == AppLifecycleState.inactive;
    if (_appActive) _queryOnlines();
  }

  void _applyOnlines(Map<String, dynamic> evt) {
    final onlines = (evt['onlines'] as String? ?? '')
        .split(',')
        .where((s) => s.isNotEmpty)
        .toSet();
    final offlines = (evt['offlines'] as String? ?? '')
        .split(',')
        .where((s) => s.isNotEmpty)
        .toSet();
    if (onlines.isEmpty && offlines.isEmpty) return;
    for (final p in gFFI.abModel.currentAbPeers) {
      if (onlines.contains(p.id)) {
        p.online = true;
      } else if (offlines.contains(p.id)) {
        p.online = false;
      }
    }
    if (mounted) {
      gFFI.abModel.currentAbPeers.refresh();
      setState(() {});
    }
  }

  void _queryOnlines() {
    if (!gFFI.userModel.isLogin) return;
    final ids = gFFI.abModel.currentAbPeers.map((p) => p.id).toList();
    if (ids.isEmpty) return;
    bind.queryOnlines(ids: ids);
    _lastOnlineQuery = DateTime.now();
  }

  Future<void> _ensureOnlineLoop() async {
    if (!_onlineLoopStarted) {
      _onlineLoopStarted = true;
      try {
        final pub = await bind.mainIsUsingPublicServer();
        _onlineInterval = Duration(seconds: pub ? 20 : 6);
      } catch (_) {}
      _onlineTimer?.cancel();
      _onlineTimer = Timer.periodic(const Duration(seconds: 1), (_) {
        if (!_appActive || !mounted || !gFFI.userModel.isLogin) return;
        if (DateTime.now().difference(_lastOnlineQuery) >= _onlineInterval) {
          _queryOnlines();
        }
      });
    }
    _queryOnlines();
  }

  String _storeName(Peer p) {
    for (final t in p.tags) {
      final s = t.toString();
      final i = s.indexOf(' · ');
      if (i > 0) return '${s.substring(0, i)} · ${s.substring(i + 3)}';
    }
    if (p.device_group_name.isNotEmpty) return p.device_group_name;
    return _kUngroupedStore;
  }

  String _deviceTitle(Peer p) {
    if (p.alias.isNotEmpty) return p.alias;
    if (p.hostname.isNotEmpty) return p.hostname;
    return formatID(p.id);
  }

  String _posLabel(Peer p) {
    final a = p.alias.trim();
    if (a.isEmpty) return '';
    final looksLikePos =
        RegExp(r'^(POS[\s-]*)?[PK]\d+$|^POS[\s-]?\S+', caseSensitive: false)
            .hasMatch(a);
    if (looksLikePos) {
      return a.toUpperCase().startsWith('POS') ? a : 'POS $a';
    }
    return '';
  }

  String _letter(String name) {
    final t = name.trim();
    if (t.isEmpty) return '#';
    return t[0].toUpperCase();
  }

  String _searchQuery() => peerSearchText.value.trim();

  bool get _searching => _searchQuery().isNotEmpty;

  bool _matchesQuery(Peer p, String q) {
    if (q.isEmpty) return true;
    final ql = q.toLowerCase();
    final nq = ql.replaceAll(' ', '');
    final store = _labelStore(_storeName(p)).toLowerCase();
    final merch = _merchantKey(_storeName(p)).toLowerCase();
    final branch = _branchLabel(_storeName(p)).toLowerCase();
    final title = _deviceTitle(p).toLowerCase();
    final pos = _posLabel(p).toLowerCase().replaceAll(' ', '');
    final host = p.hostname.toLowerCase();
    final alias = p.alias.toLowerCase();
    final id = p.id.replaceAll(' ', '').toLowerCase();
    final pretty = formatID(p.id).replaceAll(' ', '').toLowerCase();
    return store.contains(ql) ||
        merch.contains(ql) ||
        branch.contains(ql) ||
        title.contains(ql) ||
        host.contains(ql) ||
        alias.contains(ql) ||
        id.contains(nq) ||
        pretty.contains(nq) ||
        pos.contains(nq);
  }

  List<_StoreGroup> _groups() {
    final q = _searchQuery();
    // Global search: ignore the leftover selected-store filter from the old tree.
    final peers = gFFI.abModel.currentAbPeers
        .where((p) => _matchesQuery(p, q))
        .toList();
    final map = <String, List<Peer>>{};
    for (final p in peers) {
      map.putIfAbsent(_storeName(p), () => []).add(p);
    }
    final stores = map.keys.toList()
      ..sort((a, b) {
        final c = _letter(a).compareTo(_letter(b));
        if (c != 0) return c;
        return a.toLowerCase().compareTo(b.toLowerCase());
      });
    return stores.map((s) {
      final list = List<Peer>.from(map[s]!);
      list.sort((a, b) => _deviceTitle(a).compareTo(_deviceTitle(b)));
      return _StoreGroup(s, _letter(s), list);
    }).toList();
  }

  String _merchantKey(String storeName) {
    if (storeName == _kUngroupedStore) return storeName;
    final i = storeName.indexOf(' · ');
    if (i > 0) return storeName.substring(0, i);
    return storeName;
  }

  String _branchLabel(String storeName) {
    if (storeName == _kUngroupedStore) return _labelStore(storeName);
    final i = storeName.indexOf(' · ');
    if (i > 0) return storeName.substring(i + 3);
    return _labelStore(storeName);
  }

  /// Merchants that have two or more store locations in the full address book.
  Set<String> _multiMerchants() {
    final byMerch = <String, Set<String>>{};
    for (final p in gFFI.abModel.currentAbPeers) {
      final full = _storeName(p);
      byMerch.putIfAbsent(_merchantKey(full), () => {}).add(full);
    }
    return byMerch.entries
        .where((e) => e.value.length >= 2)
        .map((e) => e.key)
        .toSet();
  }

  List<_MerchantGroup> _merchantGroups() {
    final multi = _multiMerchants();
    final storeGroups = _groups();
    final map = <String, List<_StoreGroup>>{};
    final order = <String>[];
    for (final g in storeGroups) {
      final mk = _merchantKey(g.name);
      final wrap = multi.contains(mk);
      final key = wrap ? mk : g.name;
      if (!map.containsKey(key)) {
        map[key] = [];
        order.add(key);
      }
      map[key]!.add(g);
    }
    return order.map((k) {
      final stores = map[k]!;
      final wrap = multi.contains(_merchantKey(stores.first.name));
      final name = wrap ? _merchantKey(stores.first.name) : stores.first.name;
      return _MerchantGroup(
        name: name,
        letter: _letter(name),
        stores: stores,
        isMerchant: wrap,
      );
    }).toList();
  }

  bool _isMerchantOpen(String name) =>
      _searching || _openMerchants.contains(name);

  bool _isStoreOpen(String name) => _searching || _openStores.contains(name);

  void _loadExpandState() {
    try {
      final m = bind.mainGetLocalOption(key: _kOpenMerchants);
      final s = bind.mainGetLocalOption(key: _kOpenStores);
      if (m.isNotEmpty) {
        final decoded = jsonDecode(m);
        if (decoded is List) {
          _openMerchants = decoded.map((e) => e.toString()).toSet();
        }
      }
      if (s.isNotEmpty) {
        final decoded = jsonDecode(s);
        if (decoded is List) {
          _openStores = decoded.map((e) => e.toString()).toSet();
        }
      }
    } catch (e) {
      debugPrint('adoluna store list expand state load: $e');
    }
  }

  Future<void> _saveExpandState() async {
    try {
      await bind.mainSetLocalOption(
          key: _kOpenMerchants, value: jsonEncode(_openMerchants.toList()));
      await bind.mainSetLocalOption(
          key: _kOpenStores, value: jsonEncode(_openStores.toList()));
    } catch (e) {
      debugPrint('adoluna store list expand state save: $e');
    }
  }

  void _toggleMerchant(String name) {
    setState(() {
      if (_openMerchants.contains(name)) {
        _openMerchants.remove(name);
      } else {
        _openMerchants.add(name);
      }
    });
    _saveExpandState();
  }

  void _toggleStore(String name) {
    setState(() {
      if (_openStores.contains(name)) {
        _openStores.remove(name);
      } else {
        _openStores.add(name);
      }
    });
    _saveExpandState();
  }

  int _peerCount(_MerchantGroup g) =>
      g.stores.fold<int>(0, (n, s) => n + s.peers.length);

  int _onlineCount(_MerchantGroup g) => g.stores
      .fold<int>(0, (n, s) => n + s.peers.where((p) => p.online).length);

  String _nDevicesOnline(int n, int online) =>
      '${translate('{} devices').replaceAll('{}', '$n')} · $online ${translate('adoluna-online')}';

  String _searchHits(List<_MerchantGroup> groups) {
    final merch = groups.where((g) => g.isMerchant).length;
    final stores = groups.fold<int>(0, (n, g) => n + g.stores.length);
    final devices = groups.fold<int>(0, (n, g) => n + _peerCount(g));
    return translate('adoluna-search-hits')
        .replaceAll('{m}', '$merch')
        .replaceAll('{s}', '$stores')
        .replaceAll('{d}', '$devices');
  }

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      final _ = gFFI.userModel.userName.value;
      final __ = gFFI.abModel.currentName.value;
      final ___ = gFFI.abModel.currentAbPeers.length;
      final ____ = gFFI.abModel.currentAbPeers.where((p) => p.online).length;
      final _____ = peerSearchText.value;
      final loading = gFFI.abModel.currentAbLoading.value;
      final groups = _merchantGroups();
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_searching)
            Padding(
              padding: const EdgeInsets.fromLTRB(4, 2, 4, 8),
              child: Text(
                groups.isEmpty
                    ? translate('adoluna-no-devices')
                    : _searchHits(groups),
                style: const TextStyle(fontSize: 12, color: Color(0xFF8E8E93)),
              ),
            ),
          Expanded(
            child: groups.isEmpty
                ? _empty(loading)
                : ListView.builder(
                    padding: const EdgeInsets.only(right: 4, bottom: 8),
                    itemCount: groups.length,
                    itemBuilder: (context, i) => _merchantBlock(groups[i]),
                  ),
          ),
        ],
      );
    });
  }

  Widget _empty(bool loading) {
    return Center(
      child: Text(
        loading
            ? translate('adoluna-loading-devices')
            : translate('adoluna-no-devices'),
        style: const TextStyle(color: Color(0xFF8E8E93)),
      ),
    );
  }

  Widget _merchantBlock(_MerchantGroup g) {
    final openMerch = _isMerchantOpen(g.name);
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        clipBehavior: Clip.antiAlias,
        child: g.isMerchant
            ? Column(
                children: [
                  _expandRow(
                    title: g.name,
                    count: _peerCount(g),
                    online: _onlineCount(g),
                    open: openMerch,
                    onTap: () {
                      if (!_searching) _toggleMerchant(g.name);
                    },
                    merchant: true,
                  ),
                  if (openMerch) ..._storeBlocks(g.stores, nested: true),
                ],
              )
            : Column(
                children: _storeBlocks(g.stores, nested: false),
              ),
      ),
    );
  }

  List<Widget> _storeBlocks(List<_StoreGroup> stores, {required bool nested}) {
    final out = <Widget>[];
    for (var i = 0; i < stores.length; i++) {
      final s = stores[i];
      final open = _isStoreOpen(s.name);
      final online = s.peers.where((p) => p.online).length;
      if (nested && i > 0) {
        out.add(const Divider(height: 1, color: Color(0xFFE5E5EA)));
      }
      out.add(_expandRow(
        title: nested ? _branchLabel(s.name) : _labelStore(s.name),
        count: s.peers.length,
        online: online,
        open: open,
        onTap: () {
          if (!_searching) _toggleStore(s.name);
        },
        nested: nested,
      ));
      if (open) {
        for (var j = 0; j < s.peers.length; j++) {
          out.add(const Divider(height: 1, color: Color(0xFFE5E5EA)));
          out.add(_deviceRow(s.peers[j], nested: nested));
        }
      }
    }
    return out;
  }

  Widget _expandRow({
    required String title,
    required int count,
    required int online,
    required bool open,
    required VoidCallback onTap,
    bool merchant = false,
    bool nested = false,
  }) {
    return Material(
      color: merchant ? const Color(0xFFFAFAFB) : Colors.white,
      child: InkWell(
        onTap: onTap,
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 42),
          child: Padding(
            padding: EdgeInsets.fromLTRB(nested ? 28 : 14, 9, 14, 9),
            child: Row(
              children: [
                _chevron(open),
                const SizedBox(width: 6),
                Expanded(
                  child: _highlighted(
                    title,
                    style: TextStyle(
                      fontSize: nested ? 14 : 15,
                      fontWeight: FontWeight.w700,
                      letterSpacing: -0.2,
                    ),
                  ),
                ),
                Text(_nDevicesOnline(count, online),
                    style: const TextStyle(
                        fontSize: 12, color: Color(0xFF8E8E93))),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _chevron(bool open) {
    return AnimatedRotation(
      turns: open ? 0.25 : 0,
      duration: const Duration(milliseconds: 160),
      child: Icon(Icons.chevron_right,
          size: 16,
          color: open ? const Color(0xFF8E8E93) : const Color(0xFFC7C7CC)),
    );
  }

  Widget _highlighted(String text, {required TextStyle style}) {
    final q = _searchQuery();
    if (q.isEmpty) {
      return Text(text,
          maxLines: 1, overflow: TextOverflow.ellipsis, style: style);
    }
    final lower = text.toLowerCase();
    final ql = q.toLowerCase();
    final spans = <TextSpan>[];
    var start = 0;
    while (true) {
      final i = lower.indexOf(ql, start);
      if (i < 0) {
        if (start < text.length) {
          spans.add(TextSpan(text: text.substring(start), style: style));
        }
        break;
      }
      if (i > start) {
        spans.add(TextSpan(text: text.substring(start, i), style: style));
      }
      spans.add(TextSpan(
        text: text.substring(i, i + q.length),
        style: style.copyWith(color: _kRed, fontWeight: FontWeight.w800),
      ));
      start = i + q.length;
    }
    return Text.rich(
      TextSpan(style: style, children: spans),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
    );
  }

  Widget _deviceRow(Peer p, {required bool nested}) {
    final pos = _posLabel(p);
    final meta = [
      if (pos.isNotEmpty) pos,
      translate('adoluna-device-id').replaceAll('{}', formatID(p.id)),
    ].join(' · ');
    return Material(
      color: Colors.white,
      child: InkWell(
        onTap: () => connect(context, p.id),
        child: Padding(
          padding: EdgeInsets.fromLTRB(nested ? 44 : 32, 8, 14, 8),
          child: Row(
            children: [
              _platformIcon(p.platform, online: p.online),
              const SizedBox(width: 12),
              Expanded(
                child: Opacity(
                  opacity: p.online ? 1 : 0.72,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: _highlighted(
                              _deviceTitle(p),
                              style: const TextStyle(
                                  fontSize: 14, fontWeight: FontWeight.w600),
                            ),
                          ),
                          const SizedBox(width: 6),
                          _onlineBadge(p.online),
                        ],
                      ),
                      const SizedBox(height: 1),
                      _highlighted(
                        meta,
                        style: const TextStyle(
                            fontSize: 12,
                            color: Color(0xFF6C6C70),
                            fontFeatures: [FontFeature.tabularFigures()]),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(width: 10),
              _connectButton(p),
            ],
          ),
        ),
      ),
    );
  }

  Widget _connectButton(Peer p) {
    return TextButton(
      onPressed: () => connect(context, p.id),
      style: TextButton.styleFrom(
        backgroundColor: _kRed,
        foregroundColor: Colors.white,
        minimumSize: const Size(0, 28),
        padding: const EdgeInsets.symmetric(horizontal: 14),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(7)),
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        visualDensity: VisualDensity.compact,
      ),
      child: Text(translate('Connect'),
          style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
    );
  }

  Widget _platformIcon(String platform, {required bool online}) {
    // Adoluna b113: online = solid brand chip + white glyph (Android #3DDC84,
    // Windows #0078D4, Apple #1C1C1E), matching the iOS 1.5.2 request.
    // Offline stays a gray chip with a gray glyph. SVG assets are already white.
    final p = platform.toLowerCase();
    late final Color bg;
    late final Color fg;
    if (!online) {
      bg = const Color(0xFFF0F0F3);
      fg = const Color(0xFF8E8E93);
    } else if (p == 'android') {
      bg = const Color(0xFF3DDC84);
      fg = const Color(0xFFFFFFFF);
    } else if (p.contains('mac') || p == 'ios') {
      bg = const Color(0xFF1C1C1E);
      fg = const Color(0xFFFFFFFF);
    } else if (p == 'linux') {
      bg = const Color(0xFFE95420);
      fg = const Color(0xFFFFFFFF);
    } else {
      bg = const Color(0xFF0078D4);
      fg = const Color(0xFFFFFFFF);
    }
    return Container(
      width: 36,
      height: 36,
      decoration:
          BoxDecoration(color: bg, borderRadius: BorderRadius.circular(9)),
      alignment: Alignment.center,
      child: ColorFiltered(
        colorFilter: ColorFilter.mode(fg, BlendMode.srcIn),
        child: getPlatformImage(platform, size: 22),
      ),
    );
  }

  Widget _onlineBadge(bool online) {
    return Container(
      height: 18,
      padding: const EdgeInsets.symmetric(horizontal: 7),
      decoration: BoxDecoration(
        color: online ? const Color(0xFFE8F8EE) : const Color(0xFFF0F0F3),
        borderRadius: BorderRadius.circular(9),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(
              color: online ? const Color(0xFF34C759) : const Color(0xFF8E8E93),
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 4),
          Text(
              online
                  ? translate('adoluna-online')
                  : translate('adoluna-offline'),
              style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                  color: online
                      ? const Color(0xFF0F7A32)
                      : const Color(0xFF6C6C70))),
        ],
      ),
    );
  }
}

class _StoreGroup {
  final String name;
  final String letter;
  final List<Peer> peers;
  _StoreGroup(this.name, this.letter, this.peers);
}

class _MerchantGroup {
  final String name;
  final String letter;
  final List<_StoreGroup> stores;
  final bool isMerchant;
  _MerchantGroup({
    required this.name,
    required this.letter,
    required this.stores,
    required this.isMerchant,
  });
}
