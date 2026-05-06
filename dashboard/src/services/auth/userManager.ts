import { UserManager } from 'oidc-client-ts'
import { readAuthConfig, toUserManagerSettings } from './config'

let instance: UserManager | undefined

export function getUserManager(): UserManager {
  if (instance === undefined) {
    instance = new UserManager(toUserManagerSettings(readAuthConfig()))
  }
  return instance
}

// Test seam — clears the cached singleton between specs.
export function resetUserManagerForTests(): void {
  instance = undefined
}
