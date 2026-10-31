"""The lighting controller's host tools (firmware/caltreat_lighting_controller/tools/src/lcctl), vendored because
deploy.sh copies only pi/: image.py, regmap.py and ota.py are copies (pi/tests/test_lcfw.py checks), client.py is
this app's own plain-socket stand-in for lcctl's pymodbus client."""
