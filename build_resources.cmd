SET PATH=%PATH%;c:\python37\Scripts;C:\Python37\Lib\site-packages\PySide2
echo Compiling resources and UI files...
call pyside2-rcc -o pvtbox_main_rc.py application/ui/pvtbox_main.qrc
call pyside2-uic -o pvtbox_main.py application/ui/pvtbox_main.ui
call pyside2-uic -o settings.py application/ui/settings.ui
call pyside2-uic -o share_files.py application/ui/share_files.ui
call pyside2-uic -o smart_sync.py application/ui/smart_sync.ui
call pyside2-uic -o device_list.py application/ui/device_list.ui
call pyside2-uic -o lost_folder_dialog.py application/ui/lost_folder_dialog.ui
call pyside2-uic -o tutorial.py application/ui/tutorial.ui
call pyside2-uic -o transfers.py application/ui/transfers.ui
call pyside2-uic -o insert_link.py application/ui/insert_link.ui
call pyside2-uic -o about.py application/ui/about.ui
call pyside2-uic -o notifications.py application/ui/notifications.ui
call pyside2-uic -o support.py application/ui/support.ui
call pyside2-uic -o collaborations.py application/ui/collaborations.ui
call protoc --python_out=service/network/browser_sharing/proto --proto_path=service/network/browser_sharing/proto service/network/browser_sharing/proto/proto.proto
echo Done
