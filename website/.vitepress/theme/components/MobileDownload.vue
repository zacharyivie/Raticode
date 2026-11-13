<script setup lang="ts">
import {
  mobileApk,
  mobileAssetUrl,
  mobileRelease,
  mobileReleaseUrl,
  mobileSigningCertificate,
} from '../../../data/releases'

// The Raticode Mobile download in the docs, from the same manifest as the home page. The
// site deploys only once this release is public (scripts/check-downloads.mjs).
</script>

<template>
  <table class="rc-mobile-download">
    <tbody>
      <tr>
        <th scope="row">Download</th>
        <td>
          <a :href="mobileAssetUrl(mobileApk.assetName)"><code>{{ mobileApk.assetName }}</code></a>
          (Raticode Mobile {{ mobileRelease.version }}, signed APK)
        </td>
      </tr>
      <tr>
        <th scope="row">Release</th>
        <td><a :href="mobileReleaseUrl">{{ mobileRelease.tag }}</a> on GitHub, with release notes</td>
      </tr>
      <tr>
        <th scope="row">SHA-256</th>
        <td>
          <a :href="mobileAssetUrl(mobileApk.checksums)"><code>{{ mobileApk.checksums }}</code></a>, beside the APK
        </td>
      </tr>
      <tr>
        <th scope="row">Signing certificate</th>
        <td><code class="rc-cert">{{ mobileSigningCertificate }}</code> (SHA-256)</td>
      </tr>
      <tr>
        <th scope="row">Needs</th>
        <td>Android 10 or newer, and Raticode on your computer</td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.rc-mobile-download th {
  white-space: nowrap;
  text-align: left;
}

.rc-cert {
  overflow-wrap: anywhere;
}
</style>
