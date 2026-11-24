package dev.raticode.mobile.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Size
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.compose.CameraXViewfinder
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.SurfaceRequest
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.lifecycle.awaitInstance
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Scans the desktop's pairing QR code with CameraX and hands each frame to ZXing core ([QrFrameDecoder]). Launch it
 * through [ScanQrContract]. It follows the same screenshot policy as the rest of the app (no FLAG_SECURE) and stays
 * behind App lock like any other Raticode screen. The scan only returns text: the caller previews the invitation and
 * the user still confirms pairing, so a QR code can never run anything by itself.
 */
class SecureCaptureActivity : ComponentActivity() {
    private val found = AtomicBoolean(false)
    private val analysis: ExecutorService = Executors.newSingleThreadExecutor()
    private val idle = Handler(Looper.getMainLooper())
    private var surface by mutableStateOf<SurfaceRequest?>(null)
    private var problem by mutableStateOf<String?>(null)
    private var askedForCamera = false
    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startCamera() else {
            Toast.makeText(this, "Raticode needs the camera to scan the invitation. Allow it in Android settings, or paste the invitation link instead.",
                Toast.LENGTH_LONG).show()
            cancel()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        // The screen stays on while the camera looks for the code, and a forgotten scanner closes after five minutes.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        idle.postDelayed({ cancel() }, IDLE_MILLIS)
        val prompt = intent.getStringExtra(ScanQrContract.EXTRA_PROMPT).orEmpty()
        setContent { RaticodeTheme { ScannerScreen(surface, prompt, problem, onClose = ::cancel) } }
        askedForCamera = savedInstanceState?.getBoolean(ASKED, false) == true
        when {
            ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED -> startCamera()
            // Turning the phone while Android asks keeps the same question open; its answer still arrives here.
            !askedForCamera -> { askedForCamera = true; cameraPermission.launch(Manifest.permission.CAMERA) }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(ASKED, askedForCamera)
    }

    override fun onDestroy() {
        idle.removeCallbacksAndMessages(null)
        // CameraX unbinds the use cases itself when this screen's lifecycle ends.
        analysis.shutdown()
        super.onDestroy()
    }

    private fun startCamera() {
        lifecycleScope.launch {
            val provider = try {
                ProcessCameraProvider.awaitInstance(this@SecureCaptureActivity)
            } catch (e: Exception) {
                problem = "The camera couldn't start. Close the scanner and paste the invitation link instead."; return@launch
            }
            val selector = when {
                provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA) -> CameraSelector.DEFAULT_BACK_CAMERA
                provider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA) -> CameraSelector.DEFAULT_FRONT_CAMERA
                else -> { problem = "This phone has no camera Raticode can use. Paste the invitation link instead."; return@launch }
            }
            val preview = Preview.Builder().build().also { it.setSurfaceProvider { request -> surface = request } }
            val frames = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                // Large enough for a long invitation's small modules, small enough to read several frames a second.
                .setResolutionSelector(ResolutionSelector.Builder().setResolutionStrategy(
                    ResolutionStrategy(Size(1280, 720), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER)).build())
                .build()
            val decoder = QrFrameDecoder()
            frames.setAnalyzer(analysis) { image -> image.use { read(decoder, it) } }
            try {
                provider.bindToLifecycle(this@SecureCaptureActivity, selector, preview, frames)
            } catch (e: Exception) {
                problem = "The camera is busy or unavailable. Close the scanner and try again, or paste the invitation link instead."
            }
        }
    }

    private fun read(decoder: QrFrameDecoder, image: ImageProxy) {
        if (found.get()) return
        val plane = image.planes.firstOrNull() ?: return
        val buffer = plane.buffer.duplicate().apply { rewind() }
        val bytes = ByteArray(buffer.remaining()).also { buffer.get(it) }
        val text = decoder.decode(bytes, image.width, image.height, plane.rowStride, plane.pixelStride) ?: return
        if (found.compareAndSet(false, true)) runOnUiThread { finishWith(text) }
    }

    private fun finishWith(text: String) {
        if (isFinishing) return
        setResult(RESULT_OK, Intent().putExtra(ScanQrContract.EXTRA_TEXT, text))
        finish()
    }

    private fun cancel() {
        if (isFinishing) return
        setResult(RESULT_CANCELED)
        finish()
    }

    private companion object {
        const val ASKED = "askedForCamera"
        const val IDLE_MILLIS = 5 * 60 * 1000L
    }
}

@Composable
private fun ScannerScreen(surface: SurfaceRequest?, prompt: String, problem: String?, onClose: () -> Unit) {
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        if (surface != null && problem == null) CameraXViewfinder(surfaceRequest = surface, modifier = Modifier.fillMaxSize())
        Box(Modifier.fillMaxSize().safeDrawingPadding()) {
            if (problem == null) {
                Box(Modifier.align(Alignment.Center).size(240.dp).border(2.dp, Color.White.copy(alpha = 0.85f), RoundedCornerShape(16.dp)))
            }
            IconButton(onClick = onClose, modifier = Modifier.align(Alignment.TopStart).padding(8.dp)) {
                Icon(Icons.Default.Close, contentDescription = "Close scanner", tint = Color.White)
            }
            val message = problem ?: prompt
            if (message.isNotEmpty()) {
                Text(message, color = Color.White, textAlign = TextAlign.Center,
                    modifier = Modifier.align(if (problem == null) Alignment.BottomCenter else Alignment.Center).fillMaxWidth()
                        .padding(24.dp).background(Color.Black.copy(alpha = 0.6f), RoundedCornerShape(12.dp)).padding(12.dp))
            }
        }
    }
}
