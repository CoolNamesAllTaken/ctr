idf_component_register(
    SRCS "main.c" "board.c" "audio.c" "reader.c" "ui.c" "serial_cmd.c" "clock.c" "nfc.c" "rc522.c" "leds.c" "net.c" "card.c" "backend.c" "tap_report.c"
         "assets/img_chipper_mark.c" "assets/img_chipper_wordmark.c" "assets/img_icon_tap.c"
         "assets/img_icon_check.c" "assets/img_icon_cross.c"
         "assets/img_icon_prohibited.c"
    INCLUDE_DIRS "." "assets"
    EMBED_FILES "../assets/Montserrat-Bold.ttf" "../assets/Montserrat-Regular.ttf"
    REQUIRES esp_driver_gpio esp_driver_i2c esp_driver_i2s esp_driver_uart esp_driver_ppa esp_driver_spi esp_lcd esp_timer esp_lvgl_port led_strip mbedtls
             app_update esp_app_format esp_eth esp_netif esp_event esp_http_client usb usb_eth net_ota mdns
)

