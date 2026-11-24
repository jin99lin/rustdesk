import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:flutter_hbb/common.dart';
import 'package:flutter_hbb/common/formatter/id_formatter.dart';
import 'package:flutter_hbb/common/widgets/login.dart';
import 'package:flutter_hbb/models/model.dart';
import 'package:flutter_hbb/models/ab_model.dart';
import 'package:flutter_hbb/common/widgets/dialog.dart';
import 'package:flutter_hbb/consts.dart';
import 'package:flutter_hbb/models/peer_model.dart';
import 'package:flutter_hbb/models/platform_model.dart';
import 'package:flutter_hbb/mobile/pages/home_page.dart';

/// Adoluna red used by the iOS controller UI (v2 mockups).
const Color kAdolunaRed = Color(0xFFFF2400);
const Color kAdolunaRedSoft = Color(0xFFFFF1EC);

String _nDevicesPicker(int n) => translate('{$n} devices');

const _kUngroupedStore = '__ungrouped__';

String _labelStore(String name) =>
    name == _kUngroupedStore ? translate('adoluna-ungrouped') : name;

/// iOS / outgoing-only technician home: search, store sections, filters, store picker.
class TechnicianDevicesPage extends StatefulWidget implements PageShape {
  TechnicianDevicesPage({super.key}) : title = translate('Devices');

  @override
  final String title;

  @override
  final Widget icon = const Icon(Icons.grid_view_rounded);

  @override
  final List<Widget> appBarActions = const [];

  @override
  State<TechnicianDevicesPage> createState() => _TechnicianDevicesPageState();
}

enum _ChipFilter { all, onlineFirst, recent, favorite }

class _TechnicianDevicesPageState extends State<TechnicianDevicesPage>
    with WidgetsBindingObserver {
  final _search = TextEditingController();
  final _storeSearch = TextEditingController();
  _ChipFilter _chip = _ChipFilter.all;
  String? _storeFilter; // null = all stores
  Set<String> _favIds = {};
  Timer? _onlineTimer;
  bool _onlineLoopStarted = false;
  bool _appActive = true;
  DateTime _lastOnlineQuery = DateTime.fromMillisecondsSinceEpoch(0);
  Duration _onlineInterval = const Duration(seconds: 6);
  static const _kQueryOnlines = 'callback_query_onlines';
  static const _kOnlineHandler = 'technician_devices';
  static const _kOpenMerchants = 'adoluna-open-merchants';
  static const _kOpenStores = 'adoluna-open-stores';
  Set<String> _openMerchants = {};
  Set<String> _openStores = {};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    platformFFI.registerEventHandler(_kQueryOnlines, _kOnlineHandler, (evt) async {
      _applyOnlineStates(evt);
    });
    _search.addListener(() => setState(() {}));
    _loadExpandState();
    _refreshFavs();
    WidgetsBinding.instance.addPostFrameCallback((_) => _bootstrap());
  }

  Future<void> _bootstrap() async {
    try {
      bind.mainLoadRecentPeers();
      bind.mainLoadFavPeers();
      await _loadBooks();
    } catch (e) {
      debugPrint('technician devices bootstrap: $e');
    }
    if (mounted) setState(() {});
  }

  Future<void> _refreshFavs() async {
    try {
      final favs = (await bind.mainGetFav()).map((e) => e.toString()).toSet();
      if (mounted) setState(() => _favIds = favs);
    } catch (_) {}
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    platformFFI.unregisterEventHandler(_kQueryOnlines, _kOnlineHandler);
    _onlineTimer?.cancel();
    _search.dispose();
    _storeSearch.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    super.didChangeAppLifecycleState(state);
    if (state == AppLifecycleState.resumed) {
      _appActive = true;
      _queryOnlines();
    } else if (state == AppLifecycleState.inactive ||
        state == AppLifecycleState.paused) {
      _appActive = false;
    }
  }

  void _applyOnlineStates(Map<String, dynamic> evt) {
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
    // peersModel also listens to this callback and may have already flipped
    // the same Peer objects. Always refresh the list so already-built tiles
    // (the ones at the top of the screen) redraw, not only items rebuilt
    // after scrolling.
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
        _onlineInterval =
            Duration(seconds: pub ? 20 : 6);
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

  String _storeLabel(String name) => _labelStore(name);

  String _nStores(int n) => translate('{$n} stores');

  String _nDevices(int n) => translate('{$n} devices');

  String _nDevicesOnline(int n, int online) =>
      '${translate('{$n} devices')} · $online ${translate('adoluna-online')}';

  Future<void> _loadBooks() async {
    if (!gFFI.userModel.isLogin) return;
    try {
      await gFFI.abModel.pullAb(force: ForcePullAb.listAndCurrent, quiet: false);
      await gFFI.abModel.selectPreferredSharedAb();
      // Keep the shared Peers model on the current book so online callbacks
      // also update the same Peer objects Android uses.
      await platformFFI.tryHandle({'name': 'load_address_book_peers'});
    } catch (e) {
      debugPrint('technician devices load books: $e');
    }
    if (mounted) setState(() {});
    await _ensureOnlineLoop();
  }

  String _deviceTitle(Peer p) {
    if (p.alias.isNotEmpty) return p.alias;
    if (p.hostname.isNotEmpty) return p.hostname;
    return formatID(p.id);
  }

  String _posLabel(Peer p) {
    final a = p.alias.trim();
    if (a.isEmpty) return '';
    // Don't repeat the title as POS when alias *is* the title and isn't a POS code.
    final looksLikePos = RegExp(r'^(POS[\s-]*)?[PK]\d+$|^POS[\s-]?\S+', caseSensitive: false)
        .hasMatch(a);
    if (looksLikePos) {
      return a.toUpperCase().startsWith('POS') ? a : 'POS $a';
    }
    return '';
  }

  String _letter(String name) {
    final t = name.trim();
    if (t.isEmpty) return '#';
    final c = t[0].toUpperCase();
    if (RegExp(r'[A-Z]').hasMatch(c)) return c;
    final py = _pinyinInitial(t[0]);
    return py ?? '#';
  }

  bool _matchesQuery(Peer p, String q) {
    if (q.isEmpty) return true;
    final nq = q.toLowerCase().replaceAll(' ', '');
    final id = p.id.replaceAll(' ', '').toLowerCase();
    final store = _storeLabel(_storeName(p)).toLowerCase();
    final title = _deviceTitle(p).toLowerCase();
    final pos = _posLabel(p).toLowerCase().replaceAll(' ', '');
    final host = p.hostname.toLowerCase();
    return store.contains(q.toLowerCase()) ||
        title.contains(q.toLowerCase()) ||
        host.contains(q.toLowerCase()) ||
        id.contains(nq) ||
        pos.replaceAll(' ', '').contains(nq) ||
        formatID(p.id).replaceAll(' ', '').toLowerCase().contains(nq);
  }

  List<Peer> _sourcePeers() {
    final ab = List<Peer>.from(gFFI.abModel.currentAbPeers);
    switch (_chip) {
      case _ChipFilter.recent:
        final recentIds = gFFI.recentPeersModel.peers.map((e) => e.id).toList();
        final byId = {for (final p in ab) p.id: p};
        final out = <Peer>[];
        for (final id in recentIds) {
          final p = byId[id];
          if (p != null) out.add(p);
        }
        return out;
      case _ChipFilter.favorite:
        return ab.where((p) => _favIds.contains(p.id)).toList();
      case _ChipFilter.all:
      case _ChipFilter.onlineFirst:
        return ab;
    }
  }

  List<_StoreGroup> _groups() {
    final q = _search.text.trim();
    var peers = _sourcePeers().where((p) => _matchesQuery(p, q)).toList();
    if (_storeFilter != null) {
      peers = peers.where((p) => _storeName(p) == _storeFilter).toList();
    }
    final map = <String, List<Peer>>{};
    for (final p in peers) {
      map.putIfAbsent(_storeName(p), () => []).add(p);
    }
    final stores = map.keys.toList()
      ..sort((a, b) {
        final la = _letter(a);
        final lb = _letter(b);
        final c = la.compareTo(lb);
        if (c != 0) return c;
        return a.toLowerCase().compareTo(b.toLowerCase());
      });
    return stores.map((s) {
      final list = List<Peer>.from(map[s]!);
      if (_chip == _ChipFilter.onlineFirst) {
        list.sort((a, b) {
          if (a.online != b.online) return a.online ? -1 : 1;
          return _deviceTitle(a).compareTo(_deviceTitle(b));
        });
      }
      return _StoreGroup(s, _letter(s), list);
    }).toList();
  }

  List<String> _allStores() {
    final names = gFFI.abModel.currentAbPeers.map(_storeName).toSet().toList()
      ..sort((a, b) {
        final c = _letter(a).compareTo(_letter(b));
        if (c != 0) return c;
        return a.toLowerCase().compareTo(b.toLowerCase());
      });
    return names;
  }

  String _merchantKey(String storeName) {
    if (storeName == _kUngroupedStore) return storeName;
    final i = storeName.indexOf(' · ');
    if (i > 0) return storeName.substring(0, i);
    return storeName;
  }

  String _branchLabel(String storeName) {
    if (storeName == _kUngroupedStore) return _storeLabel(storeName);
    final i = storeName.indexOf(' · ');
    if (i > 0) return storeName.substring(i + 3);
    return _storeLabel(storeName);
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

  List<_MerchantGroup> _merchantGroups({bool forIndex = false}) {
    final multi = _multiMerchants();
    final storeGroups = forIndex
        ? _groupsFrom(List<Peer>.from(gFFI.abModel.currentAbPeers))
        : _groups();
    // When a single store is picked, skip the merchant wrapper and show that store.
    final skipWrapper = !forIndex && _storeFilter != null;
    final map = <String, List<_StoreGroup>>{};
    final order = <String>[];
    for (final g in storeGroups) {
      final mk = _merchantKey(g.name);
      final wrap = !skipWrapper && multi.contains(mk);
      final key = wrap ? mk : g.name;
      if (!map.containsKey(key)) {
        map[key] = [];
        order.add(key);
      }
      map[key]!.add(g);
    }
    return order.map((k) {
      final stores = map[k]!;
      final wrap = !skipWrapper && multi.contains(_merchantKey(stores.first.name));
      final name = wrap ? _merchantKey(stores.first.name) : stores.first.name;
      return _MerchantGroup(
        name: name,
        letter: _letter(name),
        stores: stores,
        isMerchant: wrap,
      );
    }).toList();
  }

  List<_StoreGroup> _groupsFrom(List<Peer> peers) {
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

  List<String> _indexNames() =>
      _merchantGroups(forIndex: true).map((g) => g.name).toList();

  bool get _searching => _search.text.trim().isNotEmpty;

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
      debugPrint('technician expand state load: $e');
    }
  }

  Future<void> _saveExpandState() async {
    try {
      await bind.mainSetLocalOption(
          key: _kOpenMerchants, value: jsonEncode(_openMerchants.toList()));
      await bind.mainSetLocalOption(
          key: _kOpenStores, value: jsonEncode(_openStores.toList()));
    } catch (e) {
      debugPrint('technician expand state save: $e');
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

  String _searchHits(List<_MerchantGroup> groups) {
    final merch = groups.where((g) => g.isMerchant).length;
    final stores = groups.fold<int>(0, (n, g) => n + g.stores.length);
    final devices = groups.fold<int>(0, (n, g) => n + _peerCount(g));
    return translate('adoluna-search-hits')
        .replaceAll('{m}', '$merch')
        .replaceAll('{s}', '$stores')
        .replaceAll('{d}', '$devices');
  }

  Future<void> _toggleFav(Peer p) async {
    final favs = (await bind.mainGetFav()).map((e) => e.toString()).toList();
    if (favs.contains(p.id)) {
      favs.remove(p.id);
    } else {
      favs.add(p.id);
    }
    await bind.mainStoreFav(favs: favs);
    await _refreshFavs();
    bind.mainLoadFavPeers();
  }

  Future<void> _login() async {
    await loginDialog();
    await _loadBooks();
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      // Depend on AB + login observables so the list updates after auto-pick.
      final _ = gFFI.userModel.userName.value;
      final __ = gFFI.abModel.currentAbPeers.length;
      final ___ = gFFI.abModel.currentName.value;
      final ____ = gFFI.abModel.currentAbLoading.value;
      final _____ = gFFI.abModel.abPullError.value;
      // So Obx rebuilds when online flags change and the RxList is refreshed.
      final ______ = gFFI.abModel.currentAbPeers.where((p) => p.online).length;
      if (gFFI.userModel.userName.value.isEmpty) {
        return _LoginGate(onLogin: _login);
      }
      final isPad = MediaQuery.of(context).size.shortestSide >= 600;
      if (isPad) return _buildIpad(context);
      return _buildPhone(context);
    });
  }

  Widget _buildPhone(BuildContext context) {
    final groups = _merchantGroups();
    return Column(
      children: [
        _searchBar(),
        _chips(),
        _storeButton(),
        Expanded(
          child: RefreshIndicator(
            color: kAdolunaRed,
            onRefresh: _loadBooks,
            child: groups.isEmpty
              ? ListView(physics: const AlwaysScrollableScrollPhysics(), children: [SizedBox(height: 240, child: _empty())])
              : Stack(
                  children: [
                    ListView.builder(
                      padding: const EdgeInsets.fromLTRB(16, 0, 22, 16),
                      itemCount: groups.length + (_searching ? 1 : 0),
                      itemBuilder: (ctx, i) {
                        if (_searching) {
                          if (i == 0) {
                            return Padding(
                              padding: const EdgeInsets.fromLTRB(2, 0, 2, 8),
                              child: Text(_searchHits(groups),
                                  style: const TextStyle(
                                      fontSize: 12, color: Color(0xFF8E8E93))),
                            );
                          }
                          i -= 1;
                        }
                        final g = groups[i];
                        final showLetter =
                            i == 0 || groups[i - 1].letter != g.letter;
                        return _merchantSection(g, showLetter: showLetter);
                      },
                    ),
                    if (!_searching && _chip == _ChipFilter.all)
                      _indexRail(_indexNames()),
                  ],
                ),
          ),
        ),
      ],
    );
  }

  Widget _buildIpad(BuildContext context) {
    final stores = _allStores();
    final q = _storeSearch.text.trim().toLowerCase();
    final filtered = q.isEmpty
        ? stores
        : stores
            .where((s) =>
                s.toLowerCase().contains(q) ||
                _storeLabel(s).toLowerCase().contains(q))
            .toList();
    final selected = _storeFilter ?? (filtered.isNotEmpty ? filtered.first : null);
    // When a store is implied (first) on iPad, show its devices.
    final deviceStore = _storeFilter ?? selected;
    final merchGroups = _merchantGroups()
        .where((g) => deviceStore == null || g.stores.any((s) => s.name == deviceStore))
        .map((g) {
      if (deviceStore == null) return g;
      final only = g.stores.where((s) => s.name == deviceStore).toList();
      return _MerchantGroup(
        name: only.first.name,
        letter: g.letter,
        stores: only,
        isMerchant: false,
      );
    }).toList();
    final shown = merchGroups;

    return Row(
      children: [
        SizedBox(
          width: 320,
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
                child: _boxSearch(_storeSearch, translate('adoluna-search-stores')),
              ),
              ListTile(
                dense: true,
                selected: _storeFilter == null,
                selectedTileColor: kAdolunaRedSoft,
                title: Text(translate('adoluna-all-stores'),
                    style: const TextStyle(fontWeight: FontWeight.w600)),
                trailing: Text(_nStores(stores.length),
                    style: const TextStyle(color: Color(0xFF8E8E93), fontSize: 12)),
                onTap: () => setState(() => _storeFilter = null),
              ),
              const Divider(height: 1),
              Expanded(
                child: Stack(
                  children: [
                    ListView.builder(
                      padding: const EdgeInsets.only(right: 16, bottom: 16),
                      itemCount: filtered.length,
                      itemBuilder: (ctx, i) {
                        final name = filtered[i];
                        final letter = _letter(name);
                        final showLetter = i == 0 ||
                            _letter(filtered[i - 1]) != letter;
                        final peers = gFFI.abModel.currentAbPeers
                            .where((p) => _storeName(p) == name)
                            .toList();
                        final online = peers.where((p) => p.online).length;
                        final selectedThis = name == deviceStore && _storeFilter != null;
                        return Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            if (showLetter)
                              Container(
                                padding: const EdgeInsets.fromLTRB(12, 6, 12, 2),
                                color: const Color(0xFFFAFAFB),
                                child: Text(letter,
                                    style: const TextStyle(
                                        fontSize: 12,
                                        fontWeight: FontWeight.w700,
                                        color: Color(0xFF6C6C70))),
                              ),
                            ListTile(
                              dense: true,
                              selected: selectedThis,
                              selectedTileColor: kAdolunaRedSoft,
                              title: Text(_storeLabel(name),
                                  style: TextStyle(
                                      fontWeight: FontWeight.w600,
                                      color: selectedThis ? kAdolunaRed : null)),
                              subtitle: Text(
                                  _nDevicesOnline(peers.length, online),
                                  style: const TextStyle(fontSize: 11)),
                              trailing: Text('${peers.length}',
                                  style: const TextStyle(
                                      color: Color(0xFF8E8E93), fontSize: 12)),
                              onTap: () => setState(() => _storeFilter = name),
                            ),
                          ],
                        );
                      },
                    ),
                    _indexRail(stores, onTap: (letter) {
                      final i = filtered.indexWhere((s) => _letter(s) == letter);
                      // visual only in listView without controller; keep simple
                    }),
                  ],
                ),
              ),
            ],
          ),
        ),
        const VerticalDivider(width: 1),
        Expanded(
          child: Column(
            children: [
              _searchBar(),
              _chips(),
              Expanded(
                child: shown.isEmpty
                    ? _empty()
                    : ListView(
                        padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
                        children: [
                          if (deviceStore != null) ...[
                            Text(_storeLabel(deviceStore),
                                style: const TextStyle(
                                    fontSize: 28,
                                    fontWeight: FontWeight.w700,
                                    letterSpacing: -0.6)),
                            const SizedBox(height: 4),
                            Text(
                              _nDevices(shown.isEmpty ? 0 : _peerCount(shown.first)),
                              style: const TextStyle(
                                  color: Color(0xFF6C6C70), fontSize: 13),
                            ),
                            const SizedBox(height: 12),
                          ],
                          ...shown.map((g) => _merchantSection(g, showLetter: false)),
                        ],
                      ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _searchBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      child: _boxSearch(_search, translate('adoluna-search-devices')),
    );
  }

  Widget _boxSearch(TextEditingController c, String hint) {
    return Container(
      height: 36,
      decoration: BoxDecoration(
        color: const Color(0xFFE8E8ED),
        borderRadius: BorderRadius.circular(10),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 10),
      child: Row(
        children: [
          const Icon(Icons.search, size: 18, color: Color(0xFF8E8E93)),
          const SizedBox(width: 6),
          Expanded(
            child: TextField(
              controller: c,
              decoration: InputDecoration(
                isDense: true,
                border: InputBorder.none,
                hintText: hint,
                hintStyle: const TextStyle(color: Color(0xFF8E8E93), fontSize: 15),
              ),
              style: const TextStyle(fontSize: 15),
              textInputAction: TextInputAction.search,
            ),
          ),
          if (c.text.isNotEmpty)
            GestureDetector(
              onTap: () => setState(() => c.clear()),
              child: const Icon(Icons.cancel, size: 18, color: Color(0xFFC7C7CC)),
            ),
        ],
      ),
    );
  }

  Widget _chips() {
    Widget chip(String label, _ChipFilter f, {bool dot = false}) {
      final on = _chip == f;
      return Padding(
        padding: const EdgeInsets.only(right: 8),
        child: ChoiceChip(
          label: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (dot) ...[
                Container(
                  width: 7,
                  height: 7,
                  decoration: BoxDecoration(
                    color: on ? Colors.white : const Color(0xFF34C759),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 5),
              ],
              Text(label),
            ],
          ),
          selected: on,
          onSelected: (_) => setState(() => _chip = f),
          selectedColor: kAdolunaRed,
          labelStyle: TextStyle(
            color: on ? Colors.white : const Color(0xFF1C1C1E),
            fontWeight: FontWeight.w500,
            fontSize: 13,
          ),
          backgroundColor: Colors.white,
          side: BorderSide(color: on ? kAdolunaRed : const Color(0xFFE5E5EA)),
          visualDensity: VisualDensity.compact,
          materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            chip(translate('All'), _ChipFilter.all),
            chip(translate('adoluna-online-first'), _ChipFilter.onlineFirst, dot: true),
            chip(translate('Recent'), _ChipFilter.recent),
            chip(translate('Favorites'), _ChipFilter.favorite),
          ],
        ),
      ),
    );
  }

  Widget _storeButton() {
    final label = _storeFilter == null ? translate('adoluna-all-stores') : _storeLabel(_storeFilter!);
    final n = _allStores().length;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
      child: Material(
        color: Colors.white,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: const BorderSide(color: Color(0xFFE5E5EA)),
        ),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: _openStorePicker,
          child: SizedBox(
            height: 40,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Row(
                children: [
                  Text(translate('adoluna-stores'),
                      style: TextStyle(fontSize: 13, color: Color(0xFF8E8E93))),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      _storeFilter == null ? '${translate('adoluna-all-stores')} · ${_nStores(n)}' : label,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontSize: 15, fontWeight: FontWeight.w600),
                    ),
                  ),
                  Text('${translate('adoluna-choose')} ›',
                      style: TextStyle(fontSize: 14, color: Color(0xFF8E8E93))),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _openStorePicker() async {
    final picked = await showModalBottomSheet<String?>(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFFF2F2F7),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(14)),
      ),
      builder: (ctx) => _StorePickerSheet(
        stores: _allStores(),
        selected: _storeFilter,
        letterOf: _letter,
        countOf: (name) => gFFI.abModel.currentAbPeers
            .where((p) => _storeName(p) == name)
            .length,
        totalStores: _allStores().length,
        totalDevices: gFFI.abModel.currentAbPeers.length,
      ),
    );
    // null from dismiss keeps current; use a sentinel? We pass '' for all.
    if (!mounted) return;
    if (picked == null && _storeFilter == null) return;
    // picked == '' means 全部店铺; popping without selection returns null (no change)
    if (picked == null) return;
    setState(() => _storeFilter = picked.isEmpty ? null : picked);
  }

  Widget _merchantSection(_MerchantGroup g, {required bool showLetter}) {
    final openMerch = !g.isMerchant || _isMerchantOpen(g.name);
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (showLetter)
            Padding(
              padding: const EdgeInsets.fromLTRB(2, 8, 2, 4),
              child: Text(g.letter,
                  style: const TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w700,
                      color: Color(0xFF6C6C70))),
            ),
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(14),
            ),
            clipBehavior: Clip.antiAlias,
            child: g.isMerchant
                ? Column(
                    children: [
                      _expandRow(
                        title: g.name,
                        count: _peerCount(g),
                        online: _onlineCount(g),
                        open: openMerch,
                        onTap: () { if (!_searching) _toggleMerchant(g.name); },
                        merchant: true,
                      ),
                      if (openMerch)
                        ..._storeBlocks(g.stores, nested: true),
                    ],
                  )
                : Column(
                    children: _storeBlocks(g.stores, nested: false),
                  ),
          ),
        ],
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
        out.add(const Divider(height: 1));
      }
      out.add(_expandRow(
        title: nested ? _branchLabel(s.name) : _storeLabel(s.name),
        count: s.peers.length,
        online: online,
        open: open,
        onTap: () { if (!_searching) _toggleStore(s.name); },
        nested: nested,
      ));
      if (open) {
        out.add(const Divider(height: 1));
        for (var j = 0; j < s.peers.length; j++) {
          if (j > 0) out.add(const Divider(height: 1));
          out.add(_deviceTile(s.peers[j], padLeft: nested ? 32 : 12));
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
          constraints: const BoxConstraints(minHeight: 48),
          child: Padding(
            padding: EdgeInsets.fromLTRB(nested ? 20 : 8, 10, 12, 10),
            child: Row(
              children: [
                _chevron(open),
                const SizedBox(width: 4),
                Expanded(
                  child: _highlighted(
                    title,
                    style: TextStyle(
                      fontSize: nested ? 15 : 16,
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
          size: 18,
          color: open ? const Color(0xFF8E8E93) : const Color(0xFFC7C7CC)),
    );
  }

  Widget _highlighted(String text, {required TextStyle style}) {
    final q = _search.text.trim();
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
        style: style.copyWith(color: kAdolunaRed, fontWeight: FontWeight.w800),
      ));
      start = i + q.length;
    }
    return Text.rich(
      TextSpan(style: style, children: spans),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
    );
  }

  Widget _deviceTile(Peer p, {double padLeft = 12}) {
    final fav = _favIds.contains(p.id);
    final pos = _posLabel(p);
    final meta = [
      if (pos.isNotEmpty) pos,
      translate('adoluna-device-id').replaceAll('{}', formatID(p.id)),
    ].join(' · ');
    return InkWell(
      onTap: () => connect(context, p.id),
      child: Opacity(
        opacity: p.online ? 1 : 0.62,
        child: Padding(
          padding: EdgeInsets.fromLTRB(padLeft, 10, 4, 10),
          child: Row(
            children: [
              _platformIcon(p.platform),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: _highlighted(
                            _deviceTitle(p),
                            style: const TextStyle(
                                fontSize: 16, fontWeight: FontWeight.w600),
                          ),
                        ),
                        const SizedBox(width: 4),
                        GestureDetector(
                          onTap: () => _toggleFav(p),
                          child: Icon(
                            fav ? Icons.star : Icons.star_border,
                            size: 16,
                            color: fav
                                ? const Color(0xFFF5A623)
                                : const Color(0xFFC7C7CC),
                          ),
                        ),
                        const SizedBox(width: 6),
                        _onlineBadge(p.online),
                      ],
                    ),
                    const SizedBox(height: 2),
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
              IconButton(
                icon: const Icon(Icons.more_vert, color: Color(0xFF8E8E93)),
                onPressed: () => _showMenu(p),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _platformIcon(String platform) {
    Color bg;
    switch (platform.toLowerCase()) {
      case 'android':
        bg = const Color(0xFFE8F6EC);
        break;
      case 'windows':
        bg = const Color(0xFFE8F1FF);
        break;
      default:
        bg = const Color(0xFFF2F2F7);
    }
    return Container(
      width: 44,
      height: 44,
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(11)),
      alignment: Alignment.center,
      child: getPlatformImage(platform, size: 26),
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
          Text(online ? translate('adoluna-online') : translate('adoluna-offline'),
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

  void _showMenu(Peer p) {
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (ctx) {
        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  decoration: BoxDecoration(
                    color: const Color(0xFFF5F5F7).withOpacity(0.96),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Column(
                    children: [
                      _sheetItem(ctx, translate('Connect'), () => connect(context, p.id),
                          primary: true),
                      _sheetItem(ctx, translate('View camera'),
                          () => connect(context, p.id, isViewCamera: true)),
                      _sheetItem(ctx, translate('Terminal'),
                          () => connect(context, p.id, isTerminal: true)),
                      _sheetItem(ctx, translate('Always connect via relay'), () async {
                        final cur = await bind.mainGetPeerOption(
                            id: p.id, key: kOptionForceAlwaysRelay);
                        final next = cur == 'Y' ? 'N' : 'Y';
                        await bind.mainSetPeerOption(
                            id: p.id, key: kOptionForceAlwaysRelay, value: next);
                        if (mounted) {
                          showToast(next == 'Y' ? translate('adoluna-relay-on') : translate('adoluna-relay-off'));
                        }
                      }),
                      _sheetItem(ctx, translate('Exist in'), () {
                        final names = gFFI.abModel.idExistIn(p.id);
                        gFFI.dialogManager.show((setState, close, context) {
                          return CustomAlertDialog(
                            title: Text(translate('Exist in')),
                            content: Text(names.join(', ')),
                            actions: [
                              dialogButton('OK',
                                  icon: const Icon(Icons.done_rounded),
                                  onPressed: close),
                            ],
                          );
                        });
                      }),
                    ],
                  ),
                ),
                const SizedBox(height: 8),
                Material(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(14),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(14),
                    onTap: () => Navigator.pop(ctx),
                    child: SizedBox(
                      height: 56,
                      width: double.infinity,
                      child: Center(
                        child: Text(translate('Cancel'),
                            style: const TextStyle(
                                fontSize: 20,
                                fontWeight: FontWeight.w600,
                                color: Color(0xFF007AFF))),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _sheetItem(BuildContext ctx, String label, VoidCallback action,
      {bool primary = false}) {
    return Column(
      children: [
        InkWell(
          onTap: () {
            Navigator.pop(ctx);
            action();
          },
          child: SizedBox(
            height: 56,
            width: double.infinity,
            child: Center(
              child: Text(label,
                  style: TextStyle(
                    fontSize: 20,
                    fontWeight: primary ? FontWeight.w600 : FontWeight.w400,
                    color: primary ? kAdolunaRed : const Color(0xFF1C1C1E),
                  )),
            ),
          ),
        ),
        const Divider(height: 1, color: Color(0x283C3C43)),
      ],
    );
  }

  Widget _indexRail(List<String> stores, {void Function(String letter)? onTap}) {
    final letters = <String>[];
    for (final s in stores) {
      final l = _letter(s);
      if (letters.isEmpty || letters.last != l) letters.add(l);
    }
    if (letters.length < 3) return const SizedBox.shrink();
    return Align(
      alignment: Alignment.centerRight,
      child: Padding(
        padding: const EdgeInsets.only(right: 2),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: letters
              .map((l) => Text(l,
                  style: const TextStyle(
                      fontSize: 9,
                      fontWeight: FontWeight.w700,
                      color: Color(0xFF007AFF),
                      height: 1.28)))
              .toList(),
        ),
      ),
    );
  }

  Widget _empty() {
    final loading = gFFI.abModel.currentAbLoading.value;
    final err = gFFI.abModel.abPullError.value;
    return Center(
      child: Text(
        err.isNotEmpty
            ? err
            : (loading ? translate('adoluna-loading-devices') : translate('adoluna-no-devices')),
        style: const TextStyle(color: Color(0xFF8E8E93)),
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

class _LoginGate extends StatelessWidget {
  final Future<void> Function() onLogin;
  const _LoginGate({required this.onLogin});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: const Color(0xFFF2F2F7),
      child: Column(
        children: [
          const Spacer(),
          Image.asset('assets/adoluna-logo.png', width: 92, height: 92,
              errorBuilder: (_, __, ___) => Image.asset(
                    'assets/icon.svg',
                    width: 92,
                    height: 92,
                    errorBuilder: (_, __, ___) => Container(
                      width: 92,
                      height: 92,
                      decoration: BoxDecoration(
                        color: kAdolunaRed,
                        borderRadius: BorderRadius.circular(22),
                      ),
                      alignment: Alignment.center,
                      child: const Text('A',
                          style: TextStyle(
                              color: Colors.white,
                              fontSize: 40,
                              fontWeight: FontWeight.w800)),
                    ),
                  )),
          const SizedBox(height: 18),
          const Text('Adoluna Support',
              style: TextStyle(
                  fontSize: 28,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -0.6)),
          const SizedBox(height: 8),
          Text(translate('adoluna-login-hint'),
              style: TextStyle(fontSize: 15, color: Color(0xFF6C6C70))),
          const SizedBox(height: 36),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 28),
            child: SizedBox(
              height: 52,
              width: double.infinity,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(
                  backgroundColor: kAdolunaRed,
                  foregroundColor: Colors.white,
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14)),
                  elevation: 0,
                ),
                onPressed: onLogin,
                child: Text(translate('Login'),
                    style:
                        TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
              ),
            ),
          ),
          const SizedBox(height: 16),
          Text(translate('adoluna-server-builtin'),
              style: TextStyle(fontSize: 12, color: Color(0xFF8E8E93))),
          const Spacer(),
        ],
      ),
    );
  }
}

class _StorePickerSheet extends StatefulWidget {
  final List<String> stores;
  final String? selected;
  final String Function(String) letterOf;
  final int Function(String) countOf;
  final int totalStores;
  final int totalDevices;

  const _StorePickerSheet({
    required this.stores,
    required this.selected,
    required this.letterOf,
    required this.countOf,
    required this.totalStores,
    required this.totalDevices,
  });

  @override
  State<_StorePickerSheet> createState() => _StorePickerSheetState();
}

class _StorePickerSheetState extends State<_StorePickerSheet> {
  final _q = TextEditingController();

  @override
  void dispose() {
    _q.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final qq = _q.text.trim().toLowerCase();
    final stores = qq.isEmpty
        ? widget.stores
        : widget.stores
            .where((s) =>
                s.toLowerCase().contains(qq) ||
                _labelStore(s).toLowerCase().contains(qq))
            .toList();
    return SizedBox(
      height: MediaQuery.of(context).size.height * 0.88,
      child: Column(
        children: [
          const SizedBox(height: 8),
          Container(
              width: 36,
              height: 5,
              decoration: BoxDecoration(
                  color: const Color(0xFFC7C7CC),
                  borderRadius: BorderRadius.circular(3))),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 6, 16, 8),
            child: Row(
              children: [
                const SizedBox(width: 48),
                Expanded(
                  child: Text(translate('adoluna-select-store'),
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          fontSize: 17, fontWeight: FontWeight.w700)),
                ),
                TextButton(
                  onPressed: () => Navigator.pop(context, widget.selected ?? ''),
                  child: Text(translate('Done'),
                      style: TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFF007AFF))),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Container(
              height: 36,
              decoration: BoxDecoration(
                color: const Color(0xFFE8E8ED),
                borderRadius: BorderRadius.circular(10),
              ),
              padding: const EdgeInsets.symmetric(horizontal: 10),
              child: Row(
                children: [
                  const Icon(Icons.search, size: 18, color: Color(0xFF8E8E93)),
                  const SizedBox(width: 6),
                  Expanded(
                    child: TextField(
                      controller: _q,
                      onChanged: (_) => setState(() {}),
                      decoration: InputDecoration(
                        isDense: true,
                        border: InputBorder.none,
                        hintText: translate('adoluna-search-stores'),
                        hintStyle: const TextStyle(
                            color: Color(0xFF8E8E93), fontSize: 15),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Material(
              color: widget.selected == null
                  ? kAdolunaRedSoft
                  : Colors.white,
              borderRadius: BorderRadius.circular(12),
              child: InkWell(
                borderRadius: BorderRadius.circular(12),
                onTap: () => Navigator.pop(context, ''),
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Row(
                    children: [
                      Text(translate('adoluna-all-stores'),
                          style: TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.w600,
                              color: widget.selected == null
                                  ? kAdolunaRed
                                  : const Color(0xFF1C1C1E))),
                      const Spacer(),
                      Text('${translate('{${widget.totalStores}} stores')} · ${translate('{${widget.totalDevices}} devices')}',
                          style: const TextStyle(
                              fontSize: 13, color: Color(0xFF8E8E93))),
                    ],
                  ),
                ),
              ),
            ),
          ),
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 28),
              itemCount: stores.length,
              itemBuilder: (ctx, i) {
                final name = stores[i];
                final letter = widget.letterOf(name);
                final show = i == 0 || widget.letterOf(stores[i - 1]) != letter;
                final on = name == widget.selected;
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (show)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(2, 8, 2, 4),
                        child: Text(letter,
                            style: const TextStyle(
                                fontSize: 13,
                                fontWeight: FontWeight.w700,
                                color: Color(0xFF6C6C70))),
                      ),
                    Material(
                      color: Colors.white,
                      child: ListTile(
                        title: Text(_labelStore(name),
                            style: TextStyle(
                                fontWeight: on ? FontWeight.w600 : FontWeight.w500,
                                color: on ? kAdolunaRed : null)),
                        trailing: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(_nDevicesPicker(widget.countOf(name)),
                                style: const TextStyle(
                                    fontSize: 13, color: Color(0xFF8E8E93))),
                            if (on)
                              const Padding(
                                padding: EdgeInsets.only(left: 8),
                                child: Icon(Icons.check, color: kAdolunaRed, size: 18),
                              ),
                          ],
                        ),
                        onTap: () => Navigator.pop(context, name),
                      ),
                    ),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

/// First letter for section indexes: Latin as-is, CJK via a compact pinyin table.
String? _pinyinInitial(String ch) {
  const map = <String, String>{
    '北': 'B', '白': 'B',
    '春': 'C', '翠': 'C', '城': 'C', '川': 'C',
    '东': 'D', '大': 'D',
    '福': 'F', '枫': 'F', '凤': 'F',
    '港': 'G', '广': 'G',
    '海': 'H', '好': 'H', '荷': 'H', '花': 'H', '红': 'H', '华': 'H',
    '金': 'J', '锦': 'J', '九': 'J',
    '老': 'L', '龙': 'L', '蓝': 'L',
    '明': 'M', '美': 'M', '麦': 'M',
    '南': 'N', '牛': 'N',
    '青': 'Q', '庆': 'Q',
    '山': 'S',
    '西': 'X', '星': 'X', '湘': 'X', '新': 'X',
    '渔': 'Y', '云': 'Y', '月': 'Y', '粤': 'Y', '银': 'Y',
    '中': 'Z', '竹': 'Z',
  };
  return map[ch];
}
