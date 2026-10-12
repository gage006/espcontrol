#!/usr/bin/env python3
"""Compile Position card tap and seek routing from production definitions."""
from pathlib import Path
import subprocess
import sys
import tempfile

from generate_media_lifecycle_integration import definition

root = Path(__file__).resolve().parents[2]
headers = root / "components/espcontrol"
media = (headers / "button_grid_media.h").read_text(encoding="utf-8")
driver = (headers / "button_grid_media_driver.h").read_text(encoding="utf-8")
config = (headers / "button_grid_media_config.h").read_text(encoding="utf-8")
sliders = (headers / "button_grid_sliders.h").read_text(encoding="utf-8")
slider_context = sliders.split("struct SliderCtx {", 1)[1].split("\n};", 1)[0]
source = r'''
#include <cassert>
#include <cstdint>
#include <string>
#include <vector>
using lv_coord_t = int;
using lv_style_selector_t = int;
using lv_color_t = uint32_t;
struct lv_font_t {};
struct lv_timer_t {};
struct lv_event_t;
struct Callback { void (*fn)(lv_event_t *); int code; void *data; };
struct lv_obj_t {
  lv_obj_t *parent = nullptr;
  void *data = nullptr;
  bool clickable = false;
  int value = 0;
  std::vector<Callback> callbacks;
  std::vector<lv_obj_t *> children;
};
struct lv_event_t { lv_obj_t *target; void *data; };
constexpr int LV_PART_MAIN = 0, LV_STATE_CHECKED = 1, LV_OBJ_FLAG_CLICKABLE = 1;
constexpr int LV_OBJ_FLAG_HIDDEN = 2, LV_ALIGN_TOP_LEFT = 0, LV_ALIGN_BOTTOM_LEFT = 1;
constexpr int LV_EVENT_CLICKED = 1, LV_EVENT_RELEASED = 2, LV_EVENT_VALUE_CHANGED = 3;
void lv_obj_add_flag(lv_obj_t *o, int f) { if (f == LV_OBJ_FLAG_CLICKABLE) o->clickable = true; }
void lv_obj_clear_flag(lv_obj_t *o, int f) { if (f == LV_OBJ_FLAG_CLICKABLE) o->clickable = false; }
void *lv_obj_get_user_data(lv_obj_t *o) { return o->data; }
void lv_obj_set_user_data(lv_obj_t *o, void *p) { o->data = p; }
void *lv_event_get_user_data(lv_event_t *e) { return e->data; }
lv_obj_t *lv_event_get_target(lv_event_t *e) { return e->target; }
lv_obj_t *lv_obj_get_parent(lv_obj_t *o) { return o->parent; }
lv_obj_t *lv_obj_get_child(lv_obj_t *o, int n) { return o->children.at(n); }
void lv_obj_add_event_cb(lv_obj_t *o, void (*fn)(lv_event_t *), int code, void *data) {
  o->callbacks.push_back({fn, code, data});
}
void emit(lv_obj_t *o, int code) {
  for (const auto &cb : o->callbacks) if (cb.code == code) {
    lv_event_t e{o, cb.data}; cb.fn(&e);
  }
}
lv_obj_t *lv_label_create(lv_obj_t *parent) {
  auto *label = new lv_obj_t; label->parent = parent;
  parent->children.push_back(label); return label;
}
void lv_obj_set_style_text_font(lv_obj_t *, const lv_font_t *, int) {}
void lv_obj_set_style_text_color(lv_obj_t *, lv_color_t, int) {}
void lv_obj_set_style_bg_color(lv_obj_t *, lv_color_t, int) {}
lv_color_t lv_color_hex(uint32_t color) { return color; }
int lv_obj_get_style_radius(lv_obj_t *, int) { return 20; }
void lv_obj_move_foreground(lv_obj_t *) {}
void lv_obj_align(lv_obj_t *, int, int, int) {}
void lv_label_set_display_text(lv_obj_t *, const char *) {}
void configure_button_label_wrap(lv_obj_t *) {}
void apply_text_width_compensation(lv_obj_t *) {}
const char *espcontrol_i18n(const char *text) { return text; }
struct CardPadding { int left = 4, right = 4, top = 4, bottom = 4; };
struct ParsedCfg { std::string sensor = "position", entity = "media_player.test", precision, options, icon; };
std::string media_card_mode(const std::string &mode) { return mode; }
std::string cfg_option_value(const std::string &options, const std::string &key) {
  const std::string prefix = key + "=";
  return options.compare(0, prefix.size(), prefix) == 0 ? options.substr(prefix.size()) : "";
}
bool media_position_show_state(const ParsedCfg &) { return true; }
std::string media_action_label(const ParsedCfg &, const std::string &) { return "Position"; }
std::string media_label(const ParsedCfg &) { return "Position"; }
const char *media_default_icon(const std::string &, const std::string &) { return ""; }
void media_format_time(float, char *, size_t) {}
int lv_slider_get_value(lv_obj_t *o) { return o->value; }
int seeks = 0, play_pauses = 0;
std::string action_entity, action_mode;
void send_media_playback_action(const std::string &entity, const std::string &mode) {
  action_entity = entity; action_mode = mode; ++play_pauses;
}
void send_media_seek_action(const std::string &entity, int value, float duration) {
  assert(entity == "media_player.test" && value == 40 && duration == 100); ++seeks;
}
lv_obj_t *setup_slider_widget(lv_obj_t *btn, uint32_t, bool) {
  lv_obj_clear_flag(btn, LV_OBJ_FLAG_CLICKABLE);
  auto *fill = new lv_obj_t, *slider = new lv_obj_t;
  fill->parent = slider->parent = btn; slider->clickable = true;
  btn->children.insert(btn->children.begin(), {fill, slider}); return slider;
}
'''
source += "struct SliderCtx {" + slider_context + "\n};\n"
source += r'''
void slider_bind_geometry_refresh(lv_obj_t *, lv_obj_t *) {}
void slider_prime_media_position_fill(SliderCtx *, lv_obj_t *) {}
void slider_update_ctx_fill(SliderCtx *, lv_obj_t *, int) {}
void media_schedule_position_refresh(SliderCtx *) {}
void media_set_pending_seek_position(SliderCtx *, int) {}
void send_media_volume_action(const std::string &, int) { assert(false); }
namespace espcontrol::media { enum class TapAction : uint8_t { NONE, PLAY_PAUSE, SEEK };
'''
source += definition(config, "tap_action_from_saved")[1] + "\n}\n"
for name in ("setup_media_slider_layout", "setup_media_position_layout"):
    line, body = definition(media, name)
    source += f'\n#line {line} "button_grid_media.h"\n{body}\n'
source += r'''
struct Context {};
struct MediaControlCtx {};
struct MediaVolumeCtx {};
bool media_driver_matches(const Context &) { return true; }
bool media_control_modal_mode(const std::string &s) { return s == "control_modal"; }
bool media_playback_button_mode(const std::string &s) { return s == "play_pause" || s == "previous" || s == "next"; }
MediaControlCtx *grid_media_control_runtime_for_owner(lv_obj_t *) { return nullptr; }
void media_control_open_modal(MediaControlCtx *) { assert(false); }
void media_volume_open_modal(MediaVolumeCtx *) { assert(false); }
void send_media_playlist_action(const ParsedCfg &) { assert(false); }
'''
for name in ("media_driver_handle_click", "media_driver_handle_main_click", "media_driver_subpage_clickable"):
    line, body = definition(driver, name)
    source += f'\n#line {line} "button_grid_media_driver.h"\n{body}\n'
source += r'''
int main() {
  ParsedCfg saved;
  lv_obj_t card, icon, label;
  auto *slider = setup_media_position_layout(&card, &icon, &label, saved,
    0xFF8C00, 0x313131, nullptr, 0xFFFFFF, {});
  auto *ctx = static_cast<SliderCtx *>(slider->data);
  ctx->media_duration = 100;
  assert(card.clickable && label.clickable && ctx->media_value_lbl->clickable);
  emit(&label, LV_EVENT_CLICKED);
  emit(ctx->media_value_lbl, LV_EVENT_CLICKED);
  assert(play_pauses == 2 && action_entity == saved.entity && action_mode == "play_pause");
  assert(media_driver_handle_main_click({}, saved, &card));
  assert(media_driver_subpage_clickable(saved));
  assert(media_driver_handle_click({}, saved, &card));
  assert(play_pauses == 4);
  // Seeking remains independent: slider releases must never toggle playback.
  slider->value = 40;
  emit(slider, LV_EVENT_RELEASED);
  emit(slider, LV_EVENT_CLICKED);
  assert(seeks == 1 && play_pauses == 4);
  ctx->available = false;
  emit(&label, LV_EVENT_CLICKED);
  emit(slider, LV_EVENT_RELEASED);
  assert(seeks == 1 && play_pauses == 4);
  // Now Playing still follows its independent configured tap action.
  saved.sensor = "now_playing"; saved.precision = "progress";
  saved.options = "media_tap_action=none";
  assert(!media_driver_subpage_clickable(saved));
  media_driver_handle_main_click({}, saved, &card);
  assert(play_pauses == 4);
  saved.options = "media_tap_action=seek";
  assert(!media_driver_subpage_clickable(saved));
  media_driver_handle_main_click({}, saved, &card);
  assert(play_pauses == 4);
  saved.options = "media_tap_action=play_pause";
  assert(media_driver_subpage_clickable(saved));
  media_driver_handle_main_click({}, saved, &card);
  assert(play_pauses == 5);
  delete ctx;
  for (auto *child : card.children) delete child;
}
'''
with tempfile.TemporaryDirectory(prefix="media-position-tap-") as directory:
    path = Path(directory)
    (path / "test.cpp").write_text(source, encoding="utf-8")
    subprocess.run([sys.argv[1] if len(sys.argv) > 1 else "g++", "-std=c++17",
                    "-Wall", "-Wextra", "-Werror", str(path / "test.cpp"),
                    "-o", str(path / "test")], check=True)
    subprocess.run([str(path / "test")], check=True)
print("Media Position tap and seek tests passed.")
