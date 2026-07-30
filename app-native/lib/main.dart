import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'app_state.dart';
import 'screens/map_screen.dart';
import 'theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(
    ChangeNotifierProvider(
      create: (_) => AppState(),
      child: MaterialApp(
        title: 'Bike Walk Greenville',
        theme: buildTheme(),
        debugShowCheckedModeBanner: false,
        home: const MapScreen(),
      ),
    ),
  );
}
