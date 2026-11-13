import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/geist'
import '@fontsource/ibm-plex-mono/400.css'
import { render } from 'solid-js/web'
import App from './App'
import './styles.css'

const root = document.getElementById('root')

if (root === null) {
    throw new Error('Не найден корневой элемент приложения.')
}

render(() => <App />, root)
