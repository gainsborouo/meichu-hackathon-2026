import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import CardManagementView from '../views/CardManagementView.vue'
import RecommendationsView from '../views/RecommendationsView.vue'
import UploadStatementView from '../views/UploadStatementView.vue'
import CalendarView from '../views/CalendarView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
    },
    {
      path: '/upload-statement',
      name: 'upload-statement',
      component: UploadStatementView,
    },
    {
      path: '/cards',
      name: 'card-management',
      component: CardManagementView,
    },
    {
      path: '/calendar',
      name: 'calendar',
      component: CalendarView,
    },
    {
      path: '/recommendations',
      name: 'recommendations',
      component: RecommendationsView,
    },
  ],
})

export default router
