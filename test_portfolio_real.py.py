#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test de análisis real con cartera de Supabase
"""
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)

from portfolio_analyzer import PortfolioAnalyzer

USER_ID = 'ea6ef068-223a-4e59-b6bc-68d4cfb8cdc0'

print('\n' + '═' * 65)
print('  HORIZONTES — Análisis Real de Cartera')
print('═' * 65 + '\n')

try:
    analyzer = PortfolioAnalyzer.from_env()
    print(f'✓ Conectado a Supabase\n')
    
    print(f'Analizando {USER_ID}...\n')
    signals = analyzer.analyze_portfolio(USER_ID)
    
    print(f'\n╔════════════════════════════════════════════════════════╗')
    print(f'║  {len(signals):2d} SEÑALES GENERADAS                         ║')
    print(f'╚════════════════════════════════════════════════════════╝\n')
    
    if signals:
        for i, s in enumerate(signals, 1):
            print(f'{i:2d}. {s.ticker:12} | {s.signal_type.value:15} | {s.conviction:5.0f}/100')
            print(f'    → {s.razon_corta}\n')
    else:
        print('  (Sin señales accionables en este momento)\n')
        
except Exception as e:
    print(f'\n❌ Error: {type(e).__name__}')
    print(f'   {str(e)}\n')
    import traceback
    traceback.print_exc()
