import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GENBU = ROOT / "ha" / "custom_components" / "genbu"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, GENBU / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


music = load("genbu_music", "music.py")
house = load("genbu_house", "house.py")
Player = music.Player

LIVING = Player("media_player.livingroom", "Wohnzimmer", area="living_room", maker="Arylic")
OWN = Player("media_player.livingroomecho8", "livingRoomEcho8", maker="TECHO5", device_name="livingRoomEcho8")
SHOW5 = Player("media_player.genbushow5", "Jarvis Show 5", area="living_room", maker="OpenJade",
               device_name="Jarvis Show 5")


class PlaysOnTests(unittest.TestCase):
    def test_a_show_alone_in_its_room_plays_on_itself(self):
        # The bedroom Show of GENBU-93: never the living room's speaker.
        self.assertEqual(music.plays_on("", "bedroom", ["livingRoomEcho8", "livingroomecho8"], [LIVING, OWN]),
                         OWN.entity)

    def test_the_rooms_speaker_comes_before_the_show(self):
        bed = Player("media_player.bedroom", "Schlafzimmer", area="bedroom")
        self.assertEqual(music.plays_on("", "bedroom", ["livingRoomEcho8"], [LIVING, OWN, bed]), bed.entity)

    def test_another_show_is_never_the_rooms_speaker(self):
        self.assertEqual(music.room_player("living_room", [SHOW5]), "")
        self.assertEqual(music.plays_on("", "living_room", ["genbushow5"], [SHOW5, LIVING]), LIVING.entity)

    def test_a_speaker_that_is_there_comes_first(self):
        gone = Player("media_player.a", area="kitchen", available=False)
        there = Player("media_player.b", area="kitchen")
        self.assertEqual(music.room_player("kitchen", [gone, there]), there.entity)
        self.assertEqual(music.room_player("kitchen", [gone]), gone.entity)

    def test_the_own_player_is_found_by_name_or_entity(self):
        # Music Assistant's id is its own, not the Show's MAC; the name echod gave Sendspin is what links them.
        self.assertEqual(music.own_player(["Jarvis Show 5"], [SHOW5]), SHOW5.entity)
        self.assertEqual(music.own_player(["genbuShow5"], [SHOW5]), SHOW5.entity)
        self.assertEqual(music.own_player(["kitchen"], [SHOW5]), "")
        not_a_show = Player("media_player.genbushow5_2", device_name="genbuShow5")
        self.assertEqual(music.own_player(["genbuShow5"], [not_a_show]), "")

    def test_a_pick_wins_while_the_player_is_there(self):
        self.assertEqual(music.plays_on(LIVING.entity, "bedroom", ["x"], [LIVING, OWN]), LIVING.entity)
        self.assertEqual(music.plays_on("media_player.gone", "bedroom", ["livingRoomEcho8"], [LIVING, OWN]),
                         OWN.entity)
        self.assertEqual(music.plays_on(music.AUTO, "living_room", [], [LIVING]), LIVING.entity)

    def test_an_offline_room_speaker_falls_back_to_the_show(self):
        off = Player(LIVING.entity, LIVING.name, area="living_room", maker="Arylic", available=False)
        self.assertEqual(music.plays_on("", "living_room", ["Jarvis Show 5"], [off, SHOW5]), SHOW5.entity)

    def test_an_offline_pick_falls_back_to_the_room_then_the_show(self):
        off = Player("media_player.kitchen", "Küche", area="kitchen", available=False)
        self.assertEqual(music.plays_on(off.entity, "living_room", ["Jarvis Show 5"], [off, LIVING, SHOW5]),
                         LIVING.entity)
        self.assertEqual(music.plays_on(off.entity, "bedroom", ["livingRoomEcho8"], [off, LIVING, OWN]),
                         OWN.entity)

    def test_all_offline_is_still_the_first_of_them(self):
        off = Player(LIVING.entity, area="living_room", available=False)
        own = Player(SHOW5.entity, maker="OpenJade", device_name="Jarvis Show 5", available=False)
        self.assertEqual(music.plays_on("", "living_room", ["Jarvis Show 5"], [off, own]), off.entity)

    def test_no_room_and_no_own_player_is_nothing(self):
        self.assertEqual(music.plays_on("", "", ["x"], [LIVING]), "")


class FallsBackToTests(unittest.TestCase):
    def test_the_rooms_speaker_falls_back_to_the_show(self):
        self.assertEqual(music.falls_back_to("", "living_room", ["Jarvis Show 5"], [LIVING, SHOW5]), SHOW5.entity)

    def test_a_pick_falls_back_to_the_room_then_the_show(self):
        kitchen = Player("media_player.kitchen", "Küche", area="kitchen")
        self.assertEqual(music.falls_back_to(kitchen.entity, "living_room", ["Jarvis Show 5"],
                                             [kitchen, LIVING, SHOW5]), LIVING.entity)
        off = Player(LIVING.entity, LIVING.name, area="living_room", maker="Arylic", available=False)
        self.assertEqual(music.falls_back_to(kitchen.entity, "living_room", ["Jarvis Show 5"],
                                             [kitchen, off, SHOW5]), SHOW5.entity)

    def test_the_show_is_the_end_of_the_line(self):
        # Alone in its room, or while the room's speaker is offline: still the Show itself.
        self.assertEqual(music.falls_back_to("", "bedroom", ["livingRoomEcho8"], [LIVING, OWN]), OWN.entity)
        off = Player(LIVING.entity, LIVING.name, area="living_room", maker="Arylic", available=False)
        self.assertEqual(music.falls_back_to("", "living_room", ["Jarvis Show 5"], [off, SHOW5]), SHOW5.entity)

    def test_picking_the_rooms_speaker_is_not_counted_twice(self):
        self.assertEqual(music.falls_back_to(LIVING.entity, "living_room", ["Jarvis Show 5"], [LIVING, SHOW5]),
                         SHOW5.entity)

    def test_no_own_player_is_nothing(self):
        self.assertEqual(music.falls_back_to("", "living_room", ["x"], [LIVING]), "")


class HouseMusicTests(unittest.TestCase):
    def test_a_pick_is_kept_and_automatic_forgets_it(self):
        h = house.House()
        h.choose_music("dev1", LIVING.entity)
        self.assertEqual(h.music_choice("dev1"), LIVING.entity)
        h.choose_music("dev1", music.AUTO)
        self.assertEqual(h.music_choice("dev1"), "")
        self.assertEqual(h.data["music"], {})

    def test_an_older_store_gets_the_music_key(self):
        self.assertEqual(house.House({"mains": ["a"]}).data["music"], {})


if __name__ == "__main__":
    unittest.main()
