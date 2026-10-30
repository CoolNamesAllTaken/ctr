idf_component_register(
    SRCS "main.c" "board.c" "audio.c" "reader.c" "ui.c" "serial_cmd.c" "clock.c" "nfc.c" "rc522.c"
         "assets/img_chipper_mark.c" "assets/img_chipper_wordmark.c" "assets/img_icon_tap.c"
         "assets/img_icon_check.c" "assets/img_icon_cross.c"
         "assets/img_icon_prohibited.c"
    INCLUDE_DIRS "." "assets"
    EMBED_FILES "../assets/Montserrat-Bold.ttf" "../assets/Montserrat-Regular.ttf"
    REQUIRES esp_driver_gpio esp_driver_i2c esp_driver_i2s esp_driver_uart esp_driver_ppa esp_driver_spi esp_lcd esp_timer esp_lvgl_port mbedtls
)

