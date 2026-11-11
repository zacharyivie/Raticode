package dev.raticode.mobile

import android.app.Application
import android.os.Build
import dev.raticode.mobile.applock.AppLockRuntime

class RaticodeApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        // Raticode only shows a notice below Android 10, so there is nothing to lock there.
        if (Build.VERSION.SDK_INT >= 29) AppLockRuntime.install(this)
    }
}
