from pathlib import Path
import sys
import unittest
from unittest.mock import mock_open, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verificar_prontidao import porta_pronta


class TestesProntidao(unittest.TestCase):
    def test_reconhece_porta_udp_do_servidor(self):
        tabela = "sl local_address rem_address\n0: 00000000:14B4 00000000:0000\n"
        with patch("builtins.open", mock_open(read_data=tabela)):
            self.assertTrue(porta_pronta(5300))
            self.assertFalse(porta_pronta(5353))

    def test_reconhece_porta_udp_do_resolver(self):
        tabela = "sl local_address rem_address\n0: 00000000:14E9 00000000:0000\n"
        with patch("builtins.open", mock_open(read_data=tabela)):
            self.assertTrue(porta_pronta(5353))
            self.assertFalse(porta_pronta(5354))

    def test_porta_remota_nao_conta_como_porta_local(self):
        tabela = "sl local_address rem_address\n0: 00000000:ABCD 640014AC:14B4\n"
        with patch("builtins.open", mock_open(read_data=tabela)):
            self.assertFalse(porta_pronta(5300))

    def test_tabela_vazia_nao_indica_prontidao(self):
        with patch("builtins.open", mock_open(read_data="sl local_address rem_address\n\n")):
            self.assertFalse(porta_pronta(5300))

    def test_falha_de_leitura_nao_indica_prontidao(self):
        with patch("builtins.open", side_effect=OSError("Tabela indisponível")):
            self.assertFalse(porta_pronta(5300))


if __name__ == "__main__":
    unittest.main()
