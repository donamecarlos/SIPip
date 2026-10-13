"""Pruebas del reproductor de tonos (MCI) y su sustituto en memoria."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sipclient.tonos import TONES, DummyTones, TonosError, TonosPlayer, tonos_dir


class DummyTonesTestCase(unittest.TestCase):
    def test_tracks_plays_and_stops(self) -> None:
        tones = DummyTones()
        self.assertTrue(tones.play("llamada_entrante", loop=True))
        self.assertTrue(tones.play("fuera_de_servicio"))
        self.assertEqual(tones.played, [("llamada_entrante", True), ("fuera_de_servicio", False)])
        tones.stop()
        self.assertFalse(tones.playing)


class TonosPlayerTestCase(unittest.TestCase):
    def test_project_tones_directory_exists(self) -> None:
        directory = tonos_dir()
        self.assertTrue(directory.is_dir(), f"falta la carpeta tonos: {directory}")

    def test_all_configured_tones_exist(self) -> None:
        directory = tonos_dir()
        for name, filename in TONES.items():
            with self.subTest(name=name):
                path = directory / filename
                self.assertTrue(path.is_file(), f"falta el archivo: {path}")
                self.assertEqual(path.suffix.lower(), ".mp3")

    def test_path_for_maps_name_to_file(self) -> None:
        player = TonosPlayer()
        self.assertEqual(player.path_for("llamada_entrante"), tonos_dir() / "llamada entrante.mp3")
        self.assertEqual(player.path_for("telefono_colgado"), tonos_dir() / "telefono colgado.mp3")

    def test_path_for_accepts_explicit_mp3_name(self) -> None:
        player = TonosPlayer()
        self.assertEqual(player.path_for("telefono colgado.mp3"), tonos_dir() / "telefono colgado.mp3")

    def test_path_for_unknown_name_raises(self) -> None:
        player = TonosPlayer()
        with self.assertRaises(TonosError):
            player.path_for("tono_misterioso")

    def test_play_missing_file_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            player = TonosPlayer(directory=tmp)
            self.assertFalse(player.play("llamada_entrante"))
            self.assertIn("llamada_entrante", player.played)
            self.assertFalse(player.available("llamada_entrante"))


if __name__ == "__main__":
    unittest.main()