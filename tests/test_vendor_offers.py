import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from vendor_offers import OFFERS, vendor_sources
import update_prices


class TestVendorAcquisition(unittest.TestCase):
    def test_standalone_vendor_catalog_matches_python_source(self):
        import json
        catalog = json.loads((ROOT / 'site' / 'data' / 'vendor-offers.json').read_text(encoding='utf-8'))
        self.assertEqual(catalog, {'schemaVersion': 1, 'offers': OFFERS})
        self.assertEqual(catalog['offers']['arcane hot shot'][0]['vendor'], 'Kaya Velasco')
        self.assertEqual(catalog['offers']['magus revert'][0]['vendor'], 'Little Duck')

    def test_vendor_catalog_count_and_copy(self):
        self.assertEqual(len(OFFERS), 43)
        self.assertEqual(vendor_sources('Exodia Brave')[0]['vendor'], 'Hok')
        self.assertEqual(vendor_sources('Exodia Brave')[0]['cost'], 10000)
        self.assertEqual(vendor_sources('Magus Repair')[0]['faction'], 'Vox Solaris')
        self.assertEqual(vendor_sources('Magus Repair')[0]['icon'], 'vox.png')
        self.assertEqual(vendor_sources('Pax Bolt')[0]['rankName'], 'Old Mate')
        result = vendor_sources('Magus Repair')
        result[0]['vendor'] = 'CHANGED'
        self.assertEqual(vendor_sources('Magus Repair')[0]['vendor'], 'Little Duck')
        self.assertEqual(vendor_sources('not a real arcane'), [])

    def test_kaya_velasco_weekly_pix_chips(self):
        for name in ('Arcane Hot Shot', 'Arcane Universal Fallout', 'Arcane Escapist'):
            with self.subTest(name=name):
                offer=vendor_sources(name)[0]
                self.assertEqual(offer['vendor'], 'Kaya Velasco')
                self.assertEqual(offer['cost'], 5)
                self.assertEqual(offer['currency'], 'Pix Chips')
                self.assertEqual(offer['weeklyLimit'], 1)
                self.assertEqual(offer['faction'], 'The Hex')
                self.assertEqual(offer['rank'], 5)
                self.assertEqual(offer['rankName'], 'Pizza Party')
                self.assertEqual(offer['location'], 'Höllvania Central Mall')

    def test_vendor_and_drop_appear_together(self):
        records = [{'name':'Arcane Hot Shot', 'drops':[{'location':'Temporal Archimedea Gold Rewards', 'chance':8.51}]}]
        index=update_prices.build_acquisition_index(records)
        items=[{'name':'Arcane Hot Shot'}]
        update_prices.attach_acquisition(items,index)
        self.assertEqual(items[0]['vendorSources'][0]['vendor'],'Kaya Velasco')
        self.assertEqual(items[0]['acquisitionSources'][0]['chance'],8.51)

    def test_vendor_only_has_no_fake_drop_rates(self):
        index = update_prices.build_acquisition_index([
            {'name':'Exodia Brave', 'drops':[]},
            {'name':'Arcane Avenger', 'drops':[{'location':'Eidolon Hydrolyst', 'chance':11.5}]},
        ])
        items = [{'name':'Exodia Brave'}, {'name':'Arcane Avenger'}]
        update_prices.attach_acquisition(items,index)
        brave, avenger = items
        self.assertEqual(brave['acquisitionStatus'],'documented')
        self.assertEqual(brave['acquisitionSources'],[])
        self.assertEqual(brave['vendorSources'][0]['vendor'],'Hok')
        self.assertEqual(avenger['vendorSources'],[])
        self.assertEqual(len(avenger['acquisitionSources']),1)

    def test_seasonal_vendor_not_a_drop(self):
        index = update_prices.build_acquisition_index([
            {'name':'Exodia Contagion', 'drops':[{'location':'Operational Supply, Defender', 'chance':100}]}
        ])
        items=[{'name':'Exodia Contagion'}]
        update_prices.attach_acquisition(items,index)
        self.assertEqual(items[0]['acquisitionSources'],[])
        offers=items[0]['vendorSources']
        self.assertEqual({a['event'] for a in offers},{'Operation: Plague Star','Nights of Naberus'})
        self.assertEqual(offers[0]['extraCost'],'1,500 Credits')

    def test_all_faction_pngs_are_present(self):
        for name in ('ostron.png','quills.png','solaris.png','vox.png', 'pix-chip.png'):
            with self.subTest(name=name):
                folder = 'currencies' if name == 'pix-chip.png' else 'factions'
                data = (ROOT / 'site' / 'assets' / folder / name).read_bytes()
                self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))

if __name__=='__main__':
    unittest.main()
